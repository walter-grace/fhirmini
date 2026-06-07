# Mac FHIR Server — native FHIR repository + healthcare integration engine + on-device AI

A self-hosted healthcare platform that runs **natively on an Apple-Silicon Mac mini** (no
Docker), built for speed. Three layers on one box:

1. **FHIR repository** — HAPI FHIR 8.x (R4) as a native Spring Boot app on Postgres 16.
2. **Integration engine** — a Mirth-class message router: ingest over **MLLP/HL7 v2**, **S3**,
   and **HTTP**, transform (HL7 v2 → FHIR), and route to **FHIR / S3 / HTTP / MLLP** destinations,
   with a durable Postgres ledger, retries, replay, and audit.
3. **On-device AI** — MLX-powered semantic search, RAG Q&A, and free-text→FHIR extraction.
   PHI-safe by default (nothing leaves the box); OpenRouter is an optional dev-only backend.

```
        INBOUND                    ENGINE (:8088)                 OUTBOUND
  ┌────────────────┐      ┌──────────────────────────┐      ┌──────────────────┐
  │ MLLP/HL7v2 :2575│─┐    │ channel: source→transform │   ┌─▶│ FHIR repo (HAPI) │
  │ HTTP /engine/in │─┼───▶│ →route, ledger, retry,    │───┼─▶│ S3 / R2 / MinIO  │
  │ S3 poller       │─┘    │ replay, audit (Postgres)  │   ├─▶│ HTTP webhook     │
  └────────────────┘      │ HL7v2→FHIR (+MLX fallback) │   └─▶│ MLLP downstream  │
                          └──────────────────────────┘
   ┌─────────────────── native, NO Docker, Apple M-series ─────────────────────┐
   │ HAPI FHIR (:8080, ZGC)   Postgres 16 (tuned)   MLX AI (:8090, LLM :8081)   │
   └────────────────────────────────────────────────────────────────────────────┘
```

## Why native (no Docker)
Docker Desktop on Mac runs a Linux VM that reserves several GB of RAM and taxes every I/O hop.
Removing it is the single biggest performance lever on a Mac mini. Measured on a base M4
(16 GB): FHIR reads **p50 ~2.8 ms**, **~1,400 req/s**. `docker-compose.yml` is kept only as a
portable fallback. See `docs/DECISIONS.md`.

## Components & ports
| Layer | Port | Run |
|---|---|---|
| HAPI FHIR (R4) | 8080 | `scripts/run-hapi.sh` (launchd: `com.fhirmini.hapi`) |
| Integration engine API + HTTP-in | 8088 | `scripts/run-engine.sh` |
| MLLP / HL7 v2 listener | 2575 | (part of the engine) |
| MLX AI sidecar | 8090 | `scripts/run-ai.sh` |
| MLX LLM server (on-demand) | 8081 | `scripts/run-ai-llm.sh` |
| Postgres 16 | 5432 | Homebrew `postgresql@16` |

## Operate it
```bash
scripts/fhirmini status         # health of every service + data counts
scripts/fhirmini install        # load all launchd agents (autostart on boot)
scripts/fhirmini start|stop|restart
scripts/fhirmini llm start      # load the 4.3GB on-device LLM (needed for /ai/ask, /ai/extract)
scripts/fhirmini smoke          # end-to-end smoke test
scripts/fhirmini logs engine    # tail a service log
```

## Quick start
```bash
git clone https://github.com/<you>/fhirmini.git && cd fhirmini
scripts/bootstrap.sh            # JDK 21 + Maven + Postgres 16, builds HAPI, creates venv & .env
scripts/fhirmini start          # launch the whole stack (FHIR + AI + engine)
scripts/fhirmini smoke          # end-to-end check (should be all green)
curl http://127.0.0.1:8080/fhir/metadata
```
`bootstrap.sh` is idempotent. To run services without autostart, use the individual
`scripts/run-*.sh` launchers instead of `fhirmini start`.

## Load synthetic test data
Pull realistic, **synthetic** Synthea patients from a public test server into your box:
```bash
fhir-ai/.venv/bin/python scripts/load_sample_data.py 12 https://r4.smarthealthit.org
```
(References are rewritten to `urn:uuid` so Observation→Patient linkage survives. This is
synthetic data only — see the PHI note below.)

## Integration engine
Channels are defined in `engine/channels.yaml`: each is `source → transform → destination(s)`.
Every message is persisted to the `engine` schema in Postgres (durability / replay / audit).

- **Send HL7 v2 over MLLP** to `127.0.0.1:2575` → ACK, parsed to FHIR (ADT→Patient+Encounter,
  ORU→Observation, ORM→ServiceRequest), upserted into HAPI, raw archived.
- **HTTP inbound:** `POST /engine/in/<channel>`.
- **S3 inbound:** enable the `s3-dropbox` channel with a bucket (+ `endpoint_url` for R2/MinIO).
- **Inspect:** `GET /engine/messages`, `GET /engine/messages/<id>`, `POST /engine/replay/<id>`.

## On-device AI
```bash
scripts/fhirmini llm start       # load the local MLX LLM (for /ai/ask + /ai/extract)
curl -XPOST :8090/ai/index                                   # embed FHIR resources
curl -XPOST :8090/ai/search -d '{"q":"diabetes","k":5}'      # semantic search
curl -XPOST :8090/ai/ask    -d '{"q":"any cardiac risks?"}'  # RAG Q&A with citations
curl -XPOST :8090/ai/extract -d '{"text":"CKD stage 3"}'     # free text -> FHIR
```
Backend is selected by `AI_BACKEND` in `.env`: `local` (MLX, PHI-safe) or `openrouter`
(cloud — **dev/synthetic only**, hard-disabled at `PHASE=phi-readiness`).

## ⚠️ Before you ever put real patient data in this
This is a strong **technical** foundation, but running real PHI is a legal/operational process,
not a flag. At minimum: a **Cloudflare Enterprise BAA** (the free tunnel is NOT a PHI-eligible
path), FileVault at-rest encryption, physical safeguards, tamper-resistant audit retention, and
a documented risk analysis. Keep `PHASE=dev-sandbox` and use **synthetic data only** until that
gate is deliberately cleared. This is not legal advice — get qualified review.

## Files
- `scripts/run-hapi.sh` / `run-ai.sh` / `run-ai-llm.sh` / `run-engine.sh` — service launchers
- `scripts/fhirmini` — unified control (status/start/stop/smoke)
- `scripts/load_sample_data.py` — synthetic data loader
- `scripts/backup-db.sh` / `rotate-logs.sh` — daily maintenance
- `config/application.yaml` — HAPI overrides (native Postgres, tuning, audit log)
- `engine/` — integration engine (codecs, hl7map, destinations, core, server)
- `fhir-ai/app.py` — AI sidecar
- `launchd/` — autostart agents
- `docs/DECISIONS.md` — architecture decisions & gotchas · `CLAUDE.md` — guardrails
