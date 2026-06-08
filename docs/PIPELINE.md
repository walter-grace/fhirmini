# Data pipeline — govern → learn → act

The closed loop that makes fhirmini an on-prem clinical-AI appliance: ingest clinical data,
**learn from it on the box**, push results back out — **PHI never leaves the machine**.

```
  source (FHIR/HL7/DB/S3) ─INGEST→ fhirmini repo ─GOVERN→ governed set ─LEARN→ model ─ACT→ FHIR writeback + model export
        (engine inbound)                (de-id + cohort/consent)   (RAG/LoRA/ML/RL)      (engine outbound)
```

## Phase 1 — Govern (shipped)
Turn the live FHIR repo into an **"appropriate" training set**: de-identified, consent-filtered,
audited. Every learner (RAG, LoRA fine-tune, predictive ML, RL) consumes this — nothing trains
on raw PHI.

```bash
export DEID_SALT=...                       # stable secret; enables linkage across exports (in .env)
python -m pipeline.export --name diabetes-2026 --limit 50 \
    --condition 44054006 \                 # SNOMED/LOINC code scoping the cohort (optional)
    --consent tag \                        # all | tag (urn:fhirmini:consent|research) | consent (Consent resource)
    --date-mode redact                     # redact = Safe-Harbor year-only; shift = Expert-Determination
# -> datasets/diabetes-2026/<ResourceType>.ndjson + manifest.json (audit)
```

**De-identification policy** (`pipeline/deidentify.py`), HIPAA Safe-Harbor-oriented for
structured fields:
- direct identifiers removed (name, telecom, photo, contact)
- ids / identifiers / references → stable non-reversible **pseudonyms** (HMAC-salt) — linkage
  survives, re-identification doesn't
- geography → state only (optional 3-digit ZIP); all dates → **year**; age >89 → aggregated
- free-text narrative dropped (NLP scrubbing of notes is a separate, harder step)
- the **audit manifest** records exactly what was removed + a salt fingerprint (never the salt)

> ⚠️ Automated de-id covers **structured** fields only. Verify Safe Harbor or obtain Expert
> Determination before releasing real PHI. Keep `PHASE=dev-sandbox` + synthetic data until then.

## Phase 2 — Learn (next)
All four consume `datasets/<name>/`:
- **RAG-adapt** (lightest, recommended first) — index the governed set; the on-device LLM
  answers grounded in it, with citations. No training, always fresh, no PHI-memorization risk.
- **LoRA fine-tune** (MLX) — adapt a small model to site style/format/coding. RAM-bound on 16GB;
  small models or burst-to-GPU for training only (data stays local).
- **Predictive ML** — tabular models on structured FHIR (risk, readmission, coding).
- **RL** (later, carefully) — governed episodes + explicit reward/safety design.

## Phase 3 — Act (next)
- Write inferences back as FHIR (`RiskAssessment` / `Flag` / `Observation`) → engine → any system.
- Export the trained model/adapter artifact (federated-style) to other nodes.
