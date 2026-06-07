"""Outbound connectors. Each deliver_* returns (ok: bool, detail: str)."""
import os, json, socket, urllib.request, urllib.error
from . import codecs


def _pick_body(dest: dict, raw: bytes, transformed):
    """A destination can ship the original bytes or the transformed FHIR JSON."""
    if dest.get("send") == "transformed" and transformed is not None:
        return json.dumps(transformed).encode(), "application/fhir+json"
    return raw, dest.get("content_type", "text/plain")


def deliver_fhir(dest: dict, raw: bytes, transformed, ctx: dict):
    """POST the transformed FHIR (transaction Bundle or resource) to a HAPI base."""
    base = dest.get("base", ctx["fhir_base"])
    if transformed is None:
        return False, "no transformed FHIR to persist"
    data = json.dumps(transformed).encode()
    req = urllib.request.Request(base, data=data, method="POST",
                                 headers={"Content-Type": "application/fhir+json",
                                          "Accept": "application/fhir+json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode())
            statuses = [e.get("response", {}).get("status", "") for e in body.get("entry", [])]
            # a transaction returns HTTP 200 even if individual entries 4xx/5xx — check each
            bad = [s for s in statuses if s[:1] in ("4", "5")]
            if bad:
                return False, f"HTTP {r.status} but entry errors={statuses}"
            return True, f"HTTP {r.status}; entries={statuses}" if statuses else f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return False, str(e)


def deliver_http(dest: dict, raw: bytes, transformed, ctx: dict):
    body, ctype = _pick_body(dest, raw, transformed)
    headers = {"Content-Type": ctype, **dest.get("headers", {})}
    req = urllib.request.Request(dest["url"], data=body, method=dest.get("method", "POST"),
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return False, str(e)


def deliver_s3(dest: dict, raw: bytes, transformed, ctx: dict):
    """Put to S3 / S3-compatible (R2, MinIO) via endpoint_url. Credentials from env
    (AWS_ACCESS_KEY_ID/SECRET) or the standard boto3 chain."""
    import boto3
    body, ctype = _pick_body(dest, raw, transformed)
    cid = ctx.get("control_id") or str(ctx.get("message_id", "msg"))
    key = dest.get("key_template", "{channel}/{id}.txt").format(
        channel=ctx["channel"], id=ctx["message_id"], control_id=cid)
    kw = {}
    if dest.get("endpoint_url"):
        kw["endpoint_url"] = dest["endpoint_url"]
    if dest.get("region"):
        kw["region_name"] = dest["region"]
    try:
        s3 = boto3.client("s3", **kw)
        s3.put_object(Bucket=dest["bucket"], Key=key, Body=body, ContentType=ctype)
        return True, f"s3://{dest['bucket']}/{key}"
    except Exception as e:
        return False, str(e)


def deliver_file(dest: dict, raw: bytes, transformed, ctx: dict):
    """No-credentials analog to S3 — write to a local directory (great for dev/demo)."""
    body, _ = _pick_body(dest, raw, transformed)
    d = os.path.expanduser(dest["dir"])
    os.makedirs(d, exist_ok=True)
    cid = ctx.get("control_id") or str(ctx.get("message_id"))
    ext = "json" if dest.get("send") == "transformed" else "hl7"
    path = os.path.join(d, f"{ctx['channel']}-{ctx['message_id']}-{cid}.{ext}")
    with open(path, "wb") as f:
        f.write(body)
    return True, path


def deliver_mllp(dest: dict, raw: bytes, transformed, ctx: dict):
    """Forward to a downstream MLLP/HL7 listener; read its ACK."""
    body, _ = _pick_body(dest, raw, transformed)
    try:
        with socket.create_connection((dest["host"], int(dest["port"])), timeout=15) as s:
            s.sendall(codecs.mllp_wrap(body))
            s.settimeout(15)
            buf = bytearray()
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                acks, _ = codecs.mllp_extract(buf)
                if acks:
                    return True, f"ACK: {acks[0][:120].decode('utf-8','ignore')}"
        return True, "sent (no ACK received)"
    except Exception as e:
        return False, str(e)


DELIVERERS = {
    "fhir": deliver_fhir,
    "http": deliver_http,
    "s3": deliver_s3,
    "file": deliver_file,
    "mllp": deliver_mllp,
}
