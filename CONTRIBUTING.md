# Contributing to fhirmini

Thanks for your interest! fhirmini is a native (Docker-free) FHIR repository + healthcare
integration engine + on-device AI layer for Apple-Silicon Macs.

## Ground rules

- **Never include real PHI** in issues, PRs, tests, or fixtures. Use synthetic data only
  (e.g. the Synthea loader, `scripts/load_sample_data.py`). See `SECURITY.md`.
- Be respectful — see `CODE_OF_CONDUCT.md`.
- By contributing, you agree your work is licensed under the project's Apache-2.0 license.

## Getting set up

Requirements: an Apple-Silicon Mac, Homebrew, and ~8 GB free disk.

```bash
git clone <your-fork-url> fhirmini && cd fhirmini
scripts/bootstrap.sh        # installs JDK + Maven, builds HAPI, sets up Postgres + venv
cp .env.example .env        # then set a strong POSTGRES_PASSWORD
scripts/fhirmini start      # or run-hapi.sh / run-ai.sh / run-engine.sh individually
scripts/fhirmini smoke      # end-to-end check (should be all green)
```

## Project layout

| Path | What |
|---|---|
| `config/application.yaml` | HAPI overrides (native Postgres, tuning, audit log) |
| `engine/` | Integration engine — `codecs` (MLLP/raw), `hl7map` (HL7v2→FHIR), `destinations`, `core`, `server` |
| `fhir-ai/app.py` | AI sidecar (MLX embeddings, RAG, extraction) |
| `scripts/` | Launchers, control tool, data loader, maintenance |
| `launchd/` | Autostart agent templates |
| `docs/DECISIONS.md` | Architecture decisions & hard-won gotchas — **read before large changes** |

## Making changes

1. Branch off `main`.
2. Keep services loopback-bound; never hardcode machine-specific absolute paths (use
   `$ROOT`/`$HOME` or env vars — see existing scripts).
3. For HL7/FHIR mapping changes, add a sample message and verify it round-trips
   (`scripts/fhirmini smoke` covers the core paths).
4. Run `python -m py_compile` on changed Python and ensure `scripts/fhirmini smoke` passes.
5. Update `docs/DECISIONS.md` for any non-obvious design choice.
6. Open a PR with a clear description and test evidence.

## Good first issues

- Broaden HL7 v2 coverage (SIU, MDM, more PID/PV1 fields).
- `$everything` pagination in the data loader for very large patients.
- Fix/verify the HAPI Tomcat access-log valve writing.
- Add a `file`/S3 **inbound** example channel and tests.
