# fhirmini benchmarks

Goal: measure what "the most badass FHIR server" actually means — **speed per dollar with
security on** — by comparing a Mac mini running fhirmini natively against cloud GPU
infrastructure (RunPod) running the identical stack.

## Methodology

- Harness: [`bench/fhir_bench.py`](../bench/fhir_bench.py) — persistent keep-alive connection
  per worker (a new-connection-per-request generator benchmarks the OS, not the server),
  warmup excluded, p50/p95/p99 + req/s, error column must be **0** for a run to count.
- Scenarios: capability read, Patient read-by-id, name search, Observation search, and
  HL7 v2 ADT ingest over MLLP (the **full** engine pipeline: durable ledger → parse →
  HL7v2→FHIR transform → idempotent upsert into HAPI → archive → ACK).
- Dataset: identical on every target (~30 Synthea patients, ~1.2k resources).
- Server performance is measured **on-box (loopback)** on every target so WAN latency
  doesn't pollute the comparison; tunnel overhead is measured separately.
- Write scenarios are tagged (`urn:fhirmini:bench`) and purged after each run.
- Cost basis is stated per target. Mac mini: $599 M4 (16GB) amortized over 3 years + ~10W
  power ≈ **$0.026/hr**. Cloud: provider's on-demand hourly price.

## Results

### Mac mini M4 (16GB), native (no Docker) — measured 2026-06-07

| Scenario | Concurrency | req/s | p50 | p95 | p99 | errors |
|---|---:|---:|---:|---:|---:|---:|
| Patient read | 1 | 2,114 | 0.44 ms | 0.70 ms | 0.90 ms | 0 |
| Patient read | 8 | 8,896 | 0.76 ms | 1.52 ms | 1.76 ms | 0 |
| **Patient read** | **32** | **10,470** | **1.89 ms** | 5.13 ms | 29.6 ms | 0 |
| Name search | 32 | 2,969 | 6.03 ms | 35.5 ms | 119 ms | 0 |
| Observation search | 32 | 5,344 | 3.87 ms | 16.4 ms | 41.6 ms | 0 |
| HL7v2 MLLP ingest | 8 | 1,083 msg/s | 7.2 ms | 10.0 ms | 13.1 ms | 0 |
| **HL7v2 MLLP ingest** | **32** | **1,120 msg/s** | 27.9 ms | 34.7 ms | 43.2 ms | 0 |

- **Reads per $1**: ~**1.45 billion** (peak patient_read at $0.026/hr).
- Every MLLP message above is a complete ingest: persisted, transformed to FHIR, upserted,
  archived, ACKed.
- JVM: openjdk 21, generational ZGC, 2GB heap. Postgres 16 native, tuned
  (see `docs/DECISIONS.md`). Stack shares the box with other production workloads.

### RunPod (same stack via docker-compose) — TBD

| Scenario | Concurrency | req/s | p50 | p95 | p99 | $/hr | reads per $1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| _pending_ | | | | | | | |

### Cloudflare Tunnel overhead (Mac, loopback vs tunnel URL) — TBD

### AI layer (MLX on M4 vs GPU w/ vLLM) — TBD
Embeddings/sec, RAG end-to-end latency, tokens/sec — the one axis where the GPU is expected
to win raw speed; the Mac competes on $/query and keeps PHI on-device.

## Reproduce

```bash
# on any target running the stack:
python3 bench/fhir_bench.py --base http://127.0.0.1:8080/fhir --label <target> \
  --concurrency 1,8,32 --seconds 6 --mllp 127.0.0.1:2575 --cost-per-hour <rate> \
  --out results.json
# purge tagged write-scenario resources (if --writes was used):
python3 bench/fhir_bench.py --base http://127.0.0.1:8080/fhir --cleanup
```
