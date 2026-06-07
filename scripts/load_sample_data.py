#!/usr/bin/env python3
"""Load SYNTHETIC patient data from a public FHIR test server into the local HAPI.

Public test servers (default hapi.fhir.org/baseR4) hold non-real, synthetic data — fine for
dev-sandbox. This pulls N patients via Patient/$everything, rewrites internal references to
urn:uuid inside a transaction bundle (so Observation->Patient linkage survives the re-ID),
strips server-assigned id/meta, and POSTs each patient's graph to the local server.

Usage:  load_sample_data.py [N] [SOURCE_BASE] [LOCAL_BASE]
"""
import sys, json, uuid, urllib.request, urllib.error

N      = int(sys.argv[1]) if len(sys.argv) > 1 else 5
SOURCE = sys.argv[2] if len(sys.argv) > 2 else "https://hapi.fhir.org/baseR4"
LOCAL  = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:8080/fhir"


def get(url):
    req = urllib.request.Request(url, headers={"Accept": "application/fhir+json",
                                               "User-Agent": "mac-fhir-loader"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def post_bundle(bundle):
    data = json.dumps(bundle).encode()
    req = urllib.request.Request(LOCAL, data=data, method="POST",
                                 headers={"Content-Type": "application/fhir+json",
                                          "Accept": "application/fhir+json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:300]}


def rewrite_refs(obj, idmap):
    """Recursively rewrite 'Type/id' references that we are importing -> urn:uuid."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "reference" and isinstance(v, str) and v in idmap:
                obj[k] = idmap[v]
            else:
                rewrite_refs(v, idmap)
    elif isinstance(obj, list):
        for it in obj:
            rewrite_refs(it, idmap)


def main():
    print(f"Source: {SOURCE}\nLocal : {LOCAL}\nPulling {N} patients...\n")
    plist = get(f"{SOURCE}/Patient?_count={N}")
    patients = [e["resource"] for e in plist.get("entry", []) if e.get("resource", {}).get("resourceType") == "Patient"]
    total_loaded = 0
    for p in patients[:N]:
        pid = p.get("id")
        try:
            everything = get(f"{SOURCE}/Patient/{pid}/$everything?_count=200")
        except Exception as e:
            print(f"  patient {pid}: $everything failed ({e}); skipping"); continue
        resources = [e["resource"] for e in everything.get("entry", []) if "resource" in e]
        if not resources:
            continue
        # map old Type/id -> fresh urn:uuid
        idmap = {f"{r['resourceType']}/{r['id']}": f"urn:uuid:{uuid.uuid4()}"
                 for r in resources if r.get("id")}
        entries = []
        for r in resources:
            full = idmap.get(f"{r['resourceType']}/{r.get('id')}")
            r.pop("id", None); r.pop("meta", None)
            rewrite_refs(r, idmap)
            entries.append({"fullUrl": full, "resource": r,
                            "request": {"method": "POST", "url": r["resourceType"]}})
        bundle = {"resourceType": "Bundle", "type": "transaction", "entry": entries}
        st, resp = post_bundle(bundle)
        ok = st == 200 and not any(e.get("response", {}).get("status", "").startswith(("4", "5"))
                                   for e in resp.get("entry", []))
        name = (p.get("name", [{}])[0].get("family", "?")) if p.get("name") else "?"
        print(f"  patient {pid} ({name}): {len(entries)} resources -> {'OK' if ok else 'PARTIAL/FAIL'} (HTTP {st})")
        if ok:
            total_loaded += len(entries)
    print(f"\nDone. Loaded ~{total_loaded} resources from {len(patients[:N])} patients.")


if __name__ == "__main__":
    main()
