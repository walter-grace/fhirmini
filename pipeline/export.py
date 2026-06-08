"""Govern + export: pull a cohort from the FHIR repo, de-identify, and write an
'appropriate' training set (FHIR NDJSON) + an audit manifest. PHI stays on the box;
the output is de-identified and consumable by every learner (RAG / LoRA / ML / RL).

Usage:
  python -m pipeline.export --name diabetes-2026 --limit 50 \
      --condition 44054006 --consent tag --out datasets
"""
import argparse, hashlib, json, os, urllib.parse, urllib.request, urllib.error
from .deidentify import Deidentifier

FHIR_BASE = os.environ.get("FHIR_BASE", "http://127.0.0.1:8080/fhir")


def _get(path):
    req = urllib.request.Request(f"{FHIR_BASE}/{path}",
                                 headers={"Accept": "application/fhir+json", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def _patient_ids(limit, condition):
    """Cohort = patients, optionally those having a given Condition code."""
    if condition:
        b = _get(f"Condition?code={urllib.parse.quote(condition)}&_count={limit}&_elements=subject")
        ids, seen = [], set()
        for e in b.get("entry", []):
            ref = e["resource"].get("subject", {}).get("reference", "")
            pid = ref.split("/")[-1]
            if pid and pid not in seen:
                seen.add(pid); ids.append(pid)
        return ids[:limit]
    b = _get(f"Patient?_count={limit}&_elements=id")
    return [e["resource"]["id"] for e in b.get("entry", [])][:limit]


def _consent_ok(pid, policy):
    if policy == "all":
        return True
    if policy == "tag":
        b = _get(f"Patient?_id={pid}&_tag=urn:fhirmini:consent|research&_summary=count")
        return (b.get("total") or 0) > 0
    if policy == "consent":
        b = _get(f"Consent?patient=Patient/{pid}&status=active&_summary=count")
        return (b.get("total") or 0) > 0
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--condition", default="", help="SNOMED/LOINC code to scope the cohort")
    ap.add_argument("--consent", choices=["all", "tag", "consent"], default="all")
    ap.add_argument("--out", default="datasets")
    ap.add_argument("--date-mode", choices=["redact", "shift"], default="redact")
    ap.add_argument("--keep-zip3", action="store_true")
    ap.add_argument("--timestamp", default="", help="ISO timestamp for the manifest (no wall-clock here)")
    a = ap.parse_args()

    salt = os.environ.get("DEID_SALT")
    if not salt:
        raise SystemExit("Set DEID_SALT in the environment (a stable secret; enables linkage across exports).")
    deid = Deidentifier(salt=salt, date_mode=a.date_mode, keep_zip3=a.keep_zip3)

    outdir = os.path.join(a.out, a.name)
    os.makedirs(outdir, exist_ok=True)
    files, counts = {}, {}
    pids = _patient_ids(a.limit, a.condition)
    included, excluded_consent = 0, 0

    for pid in pids:
        if not _consent_ok(pid, a.consent):
            excluded_consent += 1
            continue
        included += 1
        everything = _get(f"Patient/{pid}/$everything?_count=300")
        for e in everything.get("entry", []):
            r = e.get("resource")
            if not r:
                continue
            rt = r.get("resourceType")
            clean = deid.deidentify(r)
            f = files.get(rt) or open(os.path.join(outdir, f"{rt}.ndjson"), "w")
            files[rt] = f
            f.write(json.dumps(clean) + "\n")
            counts[rt] = counts.get(rt, 0) + 1
    for f in files.values():
        f.close()

    manifest = {
        "name": a.name, "created": a.timestamp or None,
        "source": FHIR_BASE,
        "cohort": {"condition": a.condition or "all-patients", "requested": a.limit,
                   "patients_included": included, "excluded_no_consent": excluded_consent,
                   "consent_policy": a.consent},
        "deidentification": {
            "standard": "HIPAA Safe Harbor (structured fields)" if a.date_mode == "redact"
                        else "Expert-Determination style (date-shift) — NOT Safe Harbor",
            "date_mode": a.date_mode, "keep_zip3": a.keep_zip3,
            "salt_fingerprint": hashlib.sha256(salt.encode()).hexdigest()[:12],
            "fields_removed": deid.removed,
        },
        "resource_counts": counts,
        "files": {rt: f"{rt}.ndjson" for rt in counts},
        "WARNING": "De-identification is automated for STRUCTURED fields. Verify Safe Harbor "
                   "or obtain Expert Determination before releasing real PHI. Free-text narrative "
                   "was dropped, not NLP-scrubbed.",
    }
    with open(os.path.join(outdir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Exported '{a.name}' -> {outdir}")
    print(f"  patients: {included} included, {excluded_consent} excluded (consent={a.consent})")
    print(f"  resources: {sum(counts.values())} across {len(counts)} types")
    print(f"  de-id removed: {deid.removed}")
    print(f"  manifest.json written")


if __name__ == "__main__":
    main()
