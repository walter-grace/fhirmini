"""Integration-engine core: channels, durable ledger (Postgres), inbound
listeners (MLLP/raw TCP), the transform+route pipeline, and a retry sweeper."""
import os, json, asyncio, threading, urllib.request
import psycopg, yaml
from psycopg.types.json import Json
from . import codecs, hl7map
from .destinations import DELIVERERS

# --------------------------------------------------------------------------- DB ledger
_conn = None
_lock = threading.Lock()


def _conninfo():
    return (f"host={os.environ.get('PGHOST','127.0.0.1')} port={os.environ.get('PGPORT','5432')} "
            f"dbname={os.environ.get('POSTGRES_DB','hapi')} user={os.environ.get('POSTGRES_USER','hapi_admin')} "
            f"password={os.environ.get('POSTGRES_PASSWORD','')}")


SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS engine;
CREATE TABLE IF NOT EXISTS engine.messages (
  id BIGSERIAL PRIMARY KEY, channel TEXT NOT NULL, direction TEXT NOT NULL DEFAULT 'inbound',
  source TEXT, codec TEXT, msg_type TEXT, control_id TEXT,
  status TEXT NOT NULL DEFAULT 'received', attempts INT NOT NULL DEFAULT 0,
  raw BYTEA, transformed JSONB, error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS messages_status_idx  ON engine.messages(status, attempts);
CREATE INDEX IF NOT EXISTS messages_channel_idx ON engine.messages(channel, created_at DESC);
CREATE TABLE IF NOT EXISTS engine.deliveries (
  id BIGSERIAL PRIMARY KEY,
  message_id BIGINT NOT NULL REFERENCES engine.messages(id) ON DELETE CASCADE,
  destination TEXT NOT NULL, status TEXT NOT NULL, detail TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now());
"""


def _db():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg.connect(_conninfo(), autocommit=True)
        # auto-provision the ledger schema (owned by the connecting role — avoids the
        # superuser-created-tables permission trap; see docs/DECISIONS.md)
        try:
            _conn.execute(SCHEMA_SQL)
        except psycopg.errors.InsufficientPrivilege:
            pass  # schema already exists with proper grants
    return _conn


def persist_received(channel, source, codec, raw: bytes) -> int:
    with _lock, _db().cursor() as cur:
        cur.execute("INSERT INTO engine.messages(channel,source,codec,raw,status) "
                    "VALUES(%s,%s,%s,%s,'received') RETURNING id",
                    (channel, source, codec, raw))
        return cur.fetchone()[0]


def set_status(mid, status, **kw):
    cols, vals = ["status=%s", "updated_at=now()"], [status]
    for k in ("msg_type", "control_id", "error"):
        if k in kw:
            cols.append(f"{k}=%s"); vals.append(kw[k])
    if "transformed" in kw:
        cols.append("transformed=%s"); vals.append(Json(kw["transformed"]) if kw["transformed"] is not None else None)
    if kw.get("bump"):
        cols.append("attempts=attempts+1")
    vals.append(mid)
    with _lock, _db().cursor() as cur:
        cur.execute(f"UPDATE engine.messages SET {','.join(cols)} WHERE id=%s", vals)


def record_delivery(mid, destination, status, detail):
    with _lock, _db().cursor() as cur:
        cur.execute("INSERT INTO engine.deliveries(message_id,destination,status,detail) "
                    "VALUES(%s,%s,%s,%s)", (mid, destination, status, detail[:1000] if detail else None))


def get_message(mid):
    with _lock, _db().cursor() as cur:
        cur.execute("SELECT id,channel,source,codec,msg_type,control_id,status,attempts,raw,transformed,error "
                    "FROM engine.messages WHERE id=%s", (mid,))
        r = cur.fetchone()
    if not r:
        return None
    keys = ["id","channel","source","codec","msg_type","control_id","status","attempts","raw","transformed","error"]
    return dict(zip(keys, r))


def recent_messages(limit=50):
    with _lock, _db().cursor() as cur:
        cur.execute("SELECT id,channel,msg_type,control_id,status,attempts,error,created_at "
                    "FROM engine.messages ORDER BY id DESC LIMIT %s", (limit,))
        rows = cur.fetchall()
    keys = ["id","channel","msg_type","control_id","status","attempts","error","created_at"]
    return [dict(zip(keys, r)) | {"created_at": str(r[-1])} for r in rows]


def pending_retries(max_attempts=5, limit=20):
    with _lock, _db().cursor() as cur:
        cur.execute("SELECT id FROM engine.messages WHERE status='failed' AND attempts < %s "
                    "ORDER BY id LIMIT %s", (max_attempts, limit))
        return [r[0] for r in cur.fetchall()]


# --------------------------------------------------------------------------- engine
class Engine:
    def __init__(self, channels_path, fhir_base, ai_url, max_attempts=5):
        self.path = channels_path
        self.fhir_base = fhir_base
        self.ai_url = ai_url
        self.max_attempts = max_attempts
        self.channels = {}
        self.servers = []
        self.tasks = []

    def load(self):
        with open(self.path) as f:
            cfg = yaml.safe_load(f) or {}
        self.channels = {c["name"]: c for c in cfg.get("channels", []) if c.get("enabled", True)}
        return list(self.channels)

    # ---- transform ----
    def transform(self, channel_cfg, raw: bytes):
        """Returns (msg_type, control_id, transformed_or_None)."""
        t = (channel_cfg.get("transform") or {}).get("type", "none")
        if t == "hl7v2-to-fhir":
            try:
                msg = hl7map.parse(raw.decode("utf-8", "ignore"))
                mt, cid = hl7map.message_type(msg), hl7map.control_id(msg)
                bundle = hl7map.to_fhir_bundle(msg)
                if not bundle and (channel_cfg.get("transform") or {}).get("ai_fallback"):
                    bundle = self._ai_fallback(raw, channel_cfg)
                return mt, cid, (bundle or None), msg
            except Exception as e:
                return "", "", None, None
        return "", "", None, None

    def _ai_fallback(self, raw: bytes, channel_cfg):
        try:
            hint = (channel_cfg.get("transform") or {}).get("ai_resource_type", "Condition")
            data = json.dumps({"text": raw.decode("utf-8", "ignore"), "resource_type": hint}).encode()
            req = urllib.request.Request(f"{self.ai_url}/ai/extract", data=data, method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                res = json.loads(r.read().decode()).get("resource")
            if res:
                return {"resourceType": "Bundle", "type": "transaction",
                        "entry": [{"resource": res, "request": {"method": "POST", "url": res.get("resourceType", "Basic")}}]}
        except Exception:
            return None

    # ---- pipeline (blocking; run via to_thread) ----
    def process_sync(self, message_id) -> bool:
        rec = get_message(message_id)
        if not rec:
            return False
        ch = self.channels.get(rec["channel"])
        if not ch:
            set_status(message_id, "failed", error="unknown channel", bump=True)
            return False
        raw = bytes(rec["raw"])
        set_status(message_id, "processing")
        mt, cid, transformed, _ = self.transform(ch, raw)
        set_status(message_id, "processing", msg_type=mt, control_id=cid, transformed=transformed)

        ctx = {"channel": rec["channel"], "message_id": message_id, "control_id": cid,
               "fhir_base": self.fhir_base}
        all_ok = True
        for dest in ch.get("destinations", []):
            fn = DELIVERERS.get(dest["type"])
            if not fn:
                record_delivery(message_id, dest["type"], "failed", "unknown destination type")
                all_ok = False; continue
            ok, detail = fn(dest, raw, transformed, ctx)
            record_delivery(message_id, dest.get("name", dest["type"]), "delivered" if ok else "failed", detail)
            all_ok = all_ok and ok
        set_status(message_id, "delivered" if all_ok else "failed",
                   msg_type=mt, control_id=cid, error=None if all_ok else "one or more destinations failed",
                   bump=True)
        return all_ok

    # ---- inbound ----
    async def handle_bytes(self, channel, source_label, raw: bytes):
        mid = await asyncio.to_thread(persist_received, channel, source_label,
                                      (self.channels[channel].get("source") or {}).get("codec", "mllp"), raw)
        ok = await asyncio.to_thread(self.process_sync, mid)
        return mid, ok

    async def _tcp_handler(self, channel, reader, writer):
        cfg = self.channels[channel]
        codec = (cfg.get("source") or {}).get("codec", "mllp")
        extract = codecs.EXTRACTORS.get(codec, codecs.mllp_extract)
        buf = bytearray()
        peer = writer.get_extra_info("peername")
        try:
            while True:
                chunk = await reader.read(8192)
                if not chunk:
                    break
                buf += chunk
                msgs, buf = extract(buf)
                for raw in msgs:
                    mid, ok = await self.handle_bytes(channel, f"tcp:{peer}", raw)
                    if codec == "mllp":
                        try:
                            m = hl7map.parse(raw.decode("utf-8", "ignore"))
                            ack = hl7map.make_ack(m, "AA" if ok else "AE")
                        except Exception:
                            ack = "MSH|^~\\&|||||||ACK||P|2.5\rMSA|AE|1|parse error\r"
                        writer.write(codecs.mllp_wrap(ack.encode()))
                        await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    async def start(self):
        for name, cfg in self.channels.items():
            src = cfg.get("source") or {}
            if src.get("type") in ("mllp", "tcp"):
                host, port = src.get("host", "127.0.0.1"), int(src.get("port", 2575))
                srv = await asyncio.start_server(
                    lambda r, w, n=name: asyncio.create_task(self._tcp_handler(n, r, w)),
                    host, port)
                self.servers.append((name, srv, f"{host}:{port}"))
            elif src.get("type") == "s3":
                self.tasks.append(asyncio.create_task(self._s3_poller(name, cfg)))
        self.tasks.append(asyncio.create_task(self._retry_sweeper()))

    async def _s3_poller(self, channel, cfg):
        import boto3
        src = cfg["source"]
        kw = {}
        if src.get("endpoint_url"): kw["endpoint_url"] = src["endpoint_url"]
        if src.get("region"): kw["region_name"] = src["region"]
        seen = set()
        interval = int(src.get("poll_seconds", 30))
        while True:
            try:
                s3 = boto3.client("s3", **kw)
                resp = s3.list_objects_v2(Bucket=src["bucket"], Prefix=src.get("prefix", ""))
                for obj in resp.get("Contents", []):
                    key = obj["Key"]
                    if key in seen or key.endswith("/"):
                        continue
                    body = s3.get_object(Bucket=src["bucket"], Key=key)["Body"].read()
                    await self.handle_bytes(channel, f"s3:{key}", body)
                    seen.add(key)
                    if src.get("delete_after"):
                        s3.delete_object(Bucket=src["bucket"], Key=key)
            except Exception:
                pass
            await asyncio.sleep(interval)

    async def _retry_sweeper(self):
        while True:
            await asyncio.sleep(20)
            for mid in await asyncio.to_thread(pending_retries, self.max_attempts):
                await asyncio.to_thread(self.process_sync, mid)

    def stop(self):
        for _, srv, _ in self.servers:
            srv.close()
        for t in self.tasks:
            t.cancel()
