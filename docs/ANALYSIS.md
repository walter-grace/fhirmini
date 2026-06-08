# Analysis — comparative effectiveness (dialysis)

"Which medicines work?" answered as **deterministic cohort comparison** over governed FHIR data.
Metric-agnostic: any clinical metric × any medication grouping. The stats are computed in code;
an LLM may *narrate* the numbers but never derives them.

## Use it
```bash
python -m analysis.synth --n 30                 # synthetic dialysis cohort (tagged, --purge to remove)
python -m analysis.dialysis --metric phosphorus --by binder
python -m analysis.dialysis --metric hemoglobin --by esa
python -m analysis.dialysis --metric all --by binder --json   # full sweep, machine-readable
```

Example (synthetic data):
```
Phosphorus (target 3.5–5.5 mg/dL) by binder — ref: ferric citrate
  group               pts  meas    mean  %inTarget   Δvsref
  ferric citrate       10    40    4.49      87.5%   +0.0pp
  sevelamer            10    40    5.40      62.5%  -25.0pp
  calcium acetate      10    40    6.00      27.5%  -60.0pp
```

## Built-in metrics & groupings
- **Metrics** (`analysis/dialysis.py:METRICS`, one source of truth shared with the generator):
  phosphorus, hemoglobin, Kt/V, potassium, calcium, PTH, albumin, interdialytic weight gain —
  each with LOINC + target band + direction (in-range / ≥ / ≤).
- **Groupings** (`MED_CLASSES`): phosphate **binder** (sevelamer / calcium acetate / ferric
  citrate / lanthanum), **esa** (epoetin / darbepoetin). Add classes by editing the registry.

## ⚠️ Read before trusting a result
- **Observational, not risk-adjusted.** Differences are **associations, not causation** —
  confounding by indication is likely (sicker patients get different drugs). Every result
  carries this caveat in its output.
- Intended as a **quality-improvement / decision-support** tool, **not** an autonomous
  treatment recommender (that's a regulated medical device).
- Demo data is synthetic. Real use requires the governed (de-identified) cohort and clinical
  review of confounders before any decision.

## How it fits the loop
`ingest → govern → ` **`analyze`** ` → act`. Next: emit results as a FHIR `MeasureReport` and
route via the engine; and a **federated** mode where each clinic analyzes/trains locally and
only shares the model — never the data.
