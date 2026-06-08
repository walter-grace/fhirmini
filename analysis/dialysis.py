"""Dialysis comparative-effectiveness analysis.

Metric-agnostic: pick any clinical metric (phosphorus, hemoglobin, Kt/V, ...) and any
medication grouping (phosphate binder, ESA) and get a per-group outcome comparison:
n, mean, % in target, and the difference vs the reference group — computed deterministically.

ⓘ Observational, not risk-adjusted: results are ASSOCIATIONS, not causal proof (confounding
by indication). Intended for quality-improvement / decision support, not autonomous Rx.
"""
import argparse, json, os, statistics, urllib.parse, urllib.request

FHIR_BASE = os.environ.get("FHIR_BASE", "http://127.0.0.1:8080/fhir")
COHORT_TAG = os.environ.get("DIALYSIS_TAG", "urn:fhirmini:cohort|dialysis")

# --- single source of truth: lab codes + targets (the synth generator imports these) ---
# direction: "in" = good inside [low,high]; "ge" = good if >= low; "le" = good if <= high
METRICS = {
    "phosphorus": {"loinc": "2777-1",  "name": "Phosphorus", "unit": "mg/dL", "low": 3.5, "high": 5.5, "dir": "in"},
    "hemoglobin": {"loinc": "718-7",   "name": "Hemoglobin", "unit": "g/dL",  "low": 10.0, "high": 11.0, "dir": "in"},
    "ktv":        {"loinc": "70215-5", "name": "Kt/V",       "unit": "1",     "low": 1.2, "high": None, "dir": "ge"},
    "potassium":  {"loinc": "6298-4",  "name": "Potassium",  "unit": "mmol/L","low": 3.5, "high": 5.5, "dir": "in"},
    "calcium":    {"loinc": "17861-6", "name": "Calcium",    "unit": "mg/dL", "low": 8.4, "high": 10.2,"dir": "in"},
    "pth":        {"loinc": "2731-8",  "name": "PTH",        "unit": "pg/mL", "low": 150, "high": 600, "dir": "in"},
    "albumin":    {"loinc": "1751-7",  "name": "Albumin",    "unit": "g/dL",  "low": 4.0, "high": None, "dir": "ge"},
    "idwg":       {"loinc": "fm-idwg", "name": "Interdialytic weight gain", "unit": "kg", "low": None, "high": 2.5, "dir": "le"},
}
# medication groupings: group label -> keywords matched against the MedicationRequest text
MED_CLASSES = {
    "binder": {"sevelamer": ["sevelamer"], "calcium acetate": ["calcium acetate"],
               "ferric citrate": ["ferric citrate"], "lanthanum": ["lanthanum"]},
    "esa": {"epoetin": ["epoetin", "epogen"], "darbepoetin": ["darbepoetin", "aranesp"]},
}


def _get(path):
    req = urllib.request.Request(f"{FHIR_BASE}/{path}",
                                 headers={"Accept": "application/fhir+json", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def _all(path):
    """Follow Bundle 'next' links to fetch every page."""
    out, url = [], path
    while url:
        b = _get(url)
        out += [e["resource"] for e in b.get("entry", []) if "resource" in e]
        url = next((l["url"].split("/fhir/", 1)[-1] for l in b.get("link", []) if l.get("relation") == "next"), None)
    return out


def in_target(metric, v):
    lo, hi, d = metric["low"], metric["high"], metric["dir"]
    if d == "ge":
        return v >= lo
    if d == "le":
        return v <= hi
    return (lo is None or v >= lo) and (hi is None or v <= hi)


def patient_groups(med_class, tag):
    """Map patient id -> medication group for the given class (e.g. which binder)."""
    keywords = MED_CLASSES[med_class]
    groups = {}
    for mr in _all(f"MedicationRequest?_tag={urllib.parse.quote(tag)}&_count=500"):
        ref = mr.get("subject", {}).get("reference", "")
        pid = ref.split("/")[-1]
        text = (mr.get("medicationCodeableConcept", {}).get("text", "") or "").lower()
        # longest matching keyword wins — avoids substring collisions like
        # "darbepoetin" containing "epoetin"
        best, best_len = None, -1
        for label, kws in keywords.items():
            for k in kws:
                if k in text and len(k) > best_len:
                    best, best_len = label, len(k)
        if best:
            groups[pid] = best
    return groups


def compare(metric_key, med_class, tag=COHORT_TAG):
    metric = METRICS[metric_key]
    groups = patient_groups(med_class, tag)
    # gather measurements for this metric, attach the patient's med group
    buckets = {}  # group -> {"patients": set, "values": [], "in": int}
    for obs in _all(f"Observation?_tag={urllib.parse.quote(tag)}&code={metric['loinc']}&_count=2000"):
        pid = obs.get("subject", {}).get("reference", "").split("/")[-1]
        g = groups.get(pid)
        if not g:
            continue
        v = obs.get("valueQuantity", {}).get("value")
        if v is None:
            continue
        b = buckets.setdefault(g, {"patients": set(), "values": [], "in": 0})
        b["patients"].add(pid); b["values"].append(v)
        if in_target(metric, v):
            b["in"] += 1

    rows = []
    for g, b in buckets.items():
        n = len(b["values"])
        rows.append({"group": g, "patients": len(b["patients"]), "measurements": n,
                     "mean": round(statistics.mean(b["values"]), 2) if n else None,
                     "pct_in_target": round(100 * b["in"] / n, 1) if n else None})
    rows.sort(key=lambda r: (r["pct_in_target"] or 0), reverse=True)
    ref = max(rows, key=lambda r: r["measurements"], default=None)  # largest group = reference
    for r in rows:
        r["delta_in_target_vs_ref"] = (round(r["pct_in_target"] - ref["pct_in_target"], 1)
                                       if ref and r["pct_in_target"] is not None else None)
    return {
        "metric": metric["name"], "unit": metric["unit"],
        "target": f"{metric['low']}–{metric['high']} {metric['unit']}".replace("None", "∞"),
        "grouped_by": med_class, "reference_group": ref["group"] if ref else None,
        "rows": rows,
        "caveat": "Observational, not risk-adjusted — ASSOCIATION not causation (confounding by "
                  "indication likely). For quality-improvement/decision-support, not autonomous Rx.",
    }


def _print(result):
    print(f"\n{result['metric']} (target {result['target']}) by {result['grouped_by']} "
          f"— ref: {result['reference_group']}")
    print(f"  {'group':<18}{'pts':>5}{'meas':>6}{'mean':>8}{'%inTarget':>11}{'Δvsref':>9}")
    for r in result["rows"]:
        print(f"  {r['group']:<18}{r['patients']:>5}{r['measurements']:>6}"
              f"{(r['mean'] if r['mean'] is not None else '-'):>8}"
              f"{(str(r['pct_in_target'])+'%' if r['pct_in_target'] is not None else '-'):>11}"
              f"{(('+' if (r['delta_in_target_vs_ref'] or 0) >= 0 else '')+str(r['delta_in_target_vs_ref'])+'pp' if r['delta_in_target_vs_ref'] is not None else '-'):>9}")
    print(f"  ⓘ {result['caveat']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metric", default="all", choices=list(METRICS) + ["all"])
    ap.add_argument("--by", default="binder", choices=list(MED_CLASSES))
    ap.add_argument("--tag", default=COHORT_TAG)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    metrics = list(METRICS) if a.metric == "all" else [a.metric]
    results = [compare(m, a.by, a.tag) for m in metrics]
    if a.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            if r["rows"]:
                _print(r)


if __name__ == "__main__":
    main()
