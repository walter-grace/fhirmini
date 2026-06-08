"""fhirmini data pipeline — govern (de-identify + cohort/consent select) the FHIR repo
into an 'appropriate' training set that every learner (RAG, LoRA, predictive, RL) consumes.
PHI never leaves the box; the governed export is de-identified + audited."""
