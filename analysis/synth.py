"""Generate a SYNTHETIC dialysis cohort to demo the analysis engine end-to-end.

Not real data. Every resource is tagged urn:fhirmini:cohort|dialysis so it's easy to analyze
and to purge (`--purge`). A deliberate (synthetic) effect is baked in — ferric citrate gets
better phosphorus control, darbepoetin slightly better Hgb — so the comparison shows a signal.
"""
import argparse, json, random, urllib.request, uuid
from .dialysis import FHIR_BASE, METRICS

TAG = {"system": "urn:fhirmini:cohort", "code": "dialysis"}
MONTHS = ["2026-01-15", "2026-02-15", "2026-03-15", "2026-04-15"]

# (mean, sd) per metric; phosphorus/hgb overridden per-group below
BASE = {"phosphorus": (5.2, 0.7), "hemoglobin": (10.4, 0.7), "ktv": (1.5, 0.2),
        "potassium": (4.8, 0.6), "calcium": (9.2, 0.6), "pth": (350, 150),
        "albumin": (3.9, 0.4), "idwg": (2.2, 0.8)}
BINDER_P = {"ferric citrate": (4.6, 0.6), "sevelamer": (5.2, 0.7), "calcium acetate": (5.9, 0.8)}
ESA_H = {"darbepoetin": (10.6, 0.6), "epoetin": (10.1, 0.9)}


def _post(bundle):
    req = urllib.request.Request(FHIR_BASE, data=json.dumps(bundle).encode(), method="POST",
                                 headers={"Content-Type": "application/fhir+json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status


def _obs(metric_key, value, patient_url, when):
    m = METRICS[metric_key]
    return {"resource": {"resourceType": "Observation", "status": "final", "meta": {"tag": [TAG]},
                         "code": {"text": m["name"],
                                  "coding": [{"system": "http://loinc.org", "code": m["loinc"], "display": m["name"]}]},
                         "subject": {"reference": patient_url}, "effectiveDateTime": when,
                         "valueQuantity": {"value": round(value, 2), "unit": m["unit"]}},
            "request": {"method": "POST", "url": "Observation"}}


def _med(text, patient_url):
    return {"resource": {"resourceType": "MedicationRequest", "status": "active", "intent": "order",
                         "meta": {"tag": [TAG]}, "medicationCodeableConcept": {"text": text},
                         "subject": {"reference": patient_url}},
            "request": {"method": "POST", "url": "MedicationRequest"}}


def generate(n, seed=42):
    random.seed(seed)
    binders = list(BINDER_P); esas = list(ESA_H)
    made = 0
    for i in range(n):
        purl = f"urn:uuid:{uuid.uuid4()}"
        binder = binders[i % len(binders)]
        esa = esas[i % len(esas)]
        entries = [
            {"fullUrl": purl, "resource": {"resourceType": "Patient", "meta": {"tag": [TAG]},
                                           "gender": random.choice(["male", "female"]), "birthDate": "1960"},
             "request": {"method": "POST", "url": "Patient"}},
            {"resource": {"resourceType": "Condition", "meta": {"tag": [TAG]},
                          "code": {"text": "End stage renal disease",
                                   "coding": [{"system": "http://snomed.info/sct", "code": "46177005"}]},
                          "subject": {"reference": purl}},
             "request": {"method": "POST", "url": "Condition"}},
            _med(binder, purl), _med(esa, purl),
        ]
        for when in MONTHS:
            for mk in METRICS:
                if mk == "phosphorus":
                    mean, sd = BINDER_P[binder]
                elif mk == "hemoglobin":
                    mean, sd = ESA_H[esa]
                else:
                    mean, sd = BASE[mk]
                val = max(0.1, random.gauss(mean, sd))
                entries.append(_obs(mk, val, purl, when))
        _post({"resourceType": "Bundle", "type": "transaction", "entry": entries})
        made += 1
    return made


def purge():
    """Delete every resource tagged urn:fhirmini:cohort|dialysis."""
    total = 0
    for rt in ("Observation", "MedicationRequest", "Condition", "Patient"):
        while True:
            req = urllib.request.Request(
                f"{FHIR_BASE}/{rt}?_tag=urn:fhirmini:cohort|dialysis&_count=300&_elements=id",
                headers={"Accept": "application/fhir+json", "Cache-Control": "no-cache"})
            ids = [e["resource"]["id"] for e in json.loads(urllib.request.urlopen(req, timeout=120).read()).get("entry", [])]
            if not ids:
                break
            _post({"resourceType": "Bundle", "type": "transaction",
                   "entry": [{"request": {"method": "DELETE", "url": f"{rt}/{i}"}} for i in ids]})
            total += len(ids)
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--purge", action="store_true")
    a = ap.parse_args()
    if a.purge:
        print(f"purged {purge()} synthetic dialysis resources")
    else:
        print(f"generated {generate(a.n)} synthetic dialysis patients (tag urn:fhirmini:cohort|dialysis)")


if __name__ == "__main__":
    main()
