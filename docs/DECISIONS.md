# Decisions log

## 2026-06-06 — Native-on-metal pivot (no Docker) for max speed
**Decision:** Run HAPI FHIR + Postgres natively on macOS instead of in Docker Compose.
**Why:** Docker Desktop on Mac runs everything inside a Linux VM that reserves several GB
of the 16 GB and taxes every I/O/network hop. On this base M4 mini, removing the VM is the
single biggest performance lever. The `docker-compose.yml` is retained as a fallback/portable
path but is not the primary runtime.

## 2026-06-06 — Toolchain
- JDK: **Temurin cask failed** (needs interactive sudo for its .pkg). Used Homebrew formula
  `openjdk@21` instead — pours into Cellar, no sudo. Pinned to Java **21 LTS** (HAPI/Spring
  Boot 3 is happiest there; the Maven-pulled openjdk 26 is too bleeding-edge).
  JAVA_HOME = `/opt/homebrew/opt/openjdk@21`.
- Build: cloned `hapifhir/hapi-fhir-jpaserver-starter` @ `8bf8a75` (2026-05-31) into
  `.build/`, `mvn clean package -DskipTests` → `target/ROOT.war` (361 MB). Produces
  **HAPI FHIR 8.10.0**, FHIR R4 (4.0.1). Pinned by git SHA for reproducibility.

## 2026-06-06 — Postgres tuning (native, Homebrew postgresql@16)
- Tuning lives in `$PGDATA/conf.d/10-m4-fhir.conf` (non-destructive include).
- shared_buffers=2GB, effective_cache_size=4GB, work_mem=32MB, 8 parallel workers,
  random_page_cost=1.1, jit=off (OLTP point lookups), pg_stat_statements preloaded.
- **macOS gotcha:** `effective_io_concurrency` MUST be 0 (no posix_fadvise on Darwin);
  any other value makes Postgres refuse to start.
- Role `hapi_admin` + DB `hapi` created; password in `.env` (gitignored, chmod 600).

## 2026-06-06 — JVM tuning
- Generational ZGC (`-XX:+UseZGC -XX:+ZGenerational`) for sub-ms GC pauses, heap capped
  `-Xms1g -Xmx2g` so Postgres + the MLX AI layer share 16 GB comfortably, string dedup on.
- Loopback bind enforced at the app layer too (`server.address: 127.0.0.1`), not just the tunnel.
- Bench (base M4, count search): p50 **2.8 ms**, ~**1,400 req/s** at 32 concurrency.

## 2026-06-06 — AI layer backend (pluggable, PHI-aware) — PLANNED
- Default `local` = MLX on-device (PHI-safe). Reuse already-downloaded
  `mlx-community/Qwen2.5-VL-7B-Instruct-4bit` as the text LLM; add a small embedding model.
- `openrouter` = cloud, DEV/synthetic ONLY, hard-guarded off at PHASE=phi-readiness.
- Config via `.env`: AI_BACKEND / AI_LOCAL_LLM / AI_EMBED_MODEL / OPENROUTER_*.

## 2026-06-06 — launchd autostart for HAPI: INSTALLED
- `~/Library/LaunchAgents/com.macfhir.hapi.plist` loaded (user authorized). HAPI autostarts
  on boot + KeepAlive. AI sidecar / LLM server / engine still run as manual nohup processes.

## 2026-06-06 — Pivot #2: healthcare INTEGRATION ENGINE (Mirth-class), not just a FHIR server
**User goal:** middleware that ingests over TCP (MLLP/HL7v2) + S3 + HTTP, transforms, and
routes out to S3 / HTTP / FHIR / MLLP. The FHIR server is now one node in a routing fabric.
- Built in Python (`engine/`), runs in the AI venv (psycopg, hl7, boto3, pyyaml added).
  Mgmt API + HTTP inbound on :8088 (`scripts/run-engine.sh`); MLLP listener :2575.
- **Channel** model (`engine/channels.yaml`): source → transform → destination(s).
  - Inbound connectors: MLLP/raw-TCP (codecs.py: mllp default, raw-newline, raw-lenprefix),
    HTTP (/engine/in/{channel}), S3 poller (config-gated).
  - Transform: HL7v2→FHIR (hl7map.py: ADT PID→Patient idempotent-upsert-by-MRN,
    ORU OBX→Observation); optional MLX AI fallback via /ai/extract.
  - Outbound (destinations.py): fhir (transaction bundle to HAPI), http, s3 (boto3,
    endpoint_url for R2/MinIO), file (no-creds S3 analog), mllp (with ACK read).
- **Durable ledger** in Postgres schema `engine` (messages + deliveries), retry sweeper
  (20s, attempts<5), replay API. Proven e2e: MLLP ADT→Patient + ORU→Observation in HAPI.
- **Bugs hit + fixed:** (1) `engine` tables created by superuser → hapi_admin got
  "permission denied"; fixed with GRANT + ALTER DEFAULT PRIVILEGES. (2) HL7 accessor read
  the whole field not the component → identifier/name were `MRN-9001^^^HOSP^MR` /
  `Hyrule^Zelda^A`; fixed `_s()` to stringify+split on `~`/`^` (robust to python-hl7 not
  wrapping single-value fields). (3) deliver_fhir trusted HTTP 200 on transaction bundles —
  now inspects per-entry status for 4xx/5xx. (4) ORU/ORM re-upsert was clobbering the
  ADT-authored Patient — ADT now PUTs (authoritative), ORU/ORM use POST ifNoneExist.

## 2026-06-06 — Hardening + ops
- HAPI Tomcat access log enabled in application.yaml (per-request audit; buffered:false).
  NOTE: file is created but staying 0-byte — valve config needs follow-up; engine ledger +
  service logs cover audit meanwhile.
- `scripts/macfhir` unified control (status/start/stop/restart/install/smoke/logs/llm).
- `scripts/smoke-test.sh` — 9-check e2e (FHIR CRUD + AI search + MLLP→FHIR). Passing 9/9.
- Backups scheduled via `com.macfhir.backup.plist` (daily 02:30) which also rotates logs
  (`scripts/rotate-logs.sh`, 10MB/3-gen).
- launchd plists written for ai/engine/backup (`launchd/`) but NOT yet installed — the auto
  classifier blocked them pending explicit user OK (only com.macfhir.hapi is authorized).

## 2026-06-06 — Synthetic data loading (user request)
- `scripts/load_sample_data.py N SOURCE LOCAL`: pulls N patients via Patient/$everything from
  a public FHIR test server, rewrites internal refs to urn:uuid in a transaction (preserves
  linkage), strips id/meta, POSTs locally. Public test-server data is SYNTHETIC (not PHI).
- Best source = `https://r4.smarthealthit.org` (Synthea: rich longitudinal histories);
  hapi.fhir.org/baseR4 is mostly thin "SamplePatientNN" junk.
- Required `enforce_referential_integrity_on_write:false` (also correct for the integration
  engine: an Observation may arrive before its Patient/Encounter). Loaded ~30 patients incl.
  675 Observations, 146 Encounters, 116 Immunizations.
- Loader limitation: patients with >200 resources truncate at one $everything page → atomic
  transaction can still 400 on a few; most load fine. Pagination is a future improvement.
- Added Immunization to the AI indexer's types + text extractor.

## 2026-06-06 — Ops lesson: orphan port-holders
- The FIRST AI sidecar (pid 77383) was never killed; every later "restart" silently failed to
  bind :8090 and died, so stale code/index kept serving. Lesson: manual nohup management is
  error-prone → this is exactly why the stack belongs under launchd (single-instance + clean
  restart). `macfhir stop` now also pkills `uvicorn app:app` / `uvicorn engine.server:app`.
