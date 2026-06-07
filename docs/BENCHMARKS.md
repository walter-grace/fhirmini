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

### RunPod Secure RTX 4090 — measured 2026-06-07

Host: AMD EPYC 75F3 (pod = the vCPU slice sold with a 1×4090 secure pod) + RTX 4090,
$0.69/hr. Identical stack built from the same HAPI git SHA on Temurin 21 + Postgres 16,
same tuning, same dataset, same harness, loopback. Raw data: [`bench/results/`](../bench/results/).

| Scenario | Concurrency | req/s | p50 | p95 | p99 | errors |
|---|---:|---:|---:|---:|---:|---:|
| Patient read | 1 | 481 | 1.81 ms | 3.48 ms | 5.42 ms | 0 |
| Patient read | 8 | 2,038 | 3.53 ms | 6.86 ms | 9.72 ms | 0 |
| **Patient read** | **32** | **2,150** | **12.3 ms** | 33.0 ms | 47.6 ms | 0 |
| Name search | 32 | 1,654 | 16.2 ms | 42.6 ms | 60.7 ms | 0 |
| Observation search | 32 | 3,087 | 7.6 ms | 25.4 ms | 39.4 ms | 0 |
| HL7v2 MLLP ingest | 8 | 324 msg/s | 21.8 ms | 48.4 ms | 64.7 ms | 0 |
| **HL7v2 MLLP ingest** | **32** | **291 msg/s** | 103.8 ms | 155.3 ms | 182.5 ms | 0 |

- **Reads per $1**: ~**11.2 million** (peak patient_read at $0.69/hr).

### Head-to-head

| Metric | Mac mini M4 ($599, 16GB) | RunPod 4090 Secure ($0.69/hr) | Mac advantage |
|---|---:|---:|---:|
| Patient read, peak req/s | **10,470** | 2,150 | **4.9×** |
| Patient read p50 (conc 1) | **0.44 ms** | 1.81 ms | **4.1×** |
| Observation search, peak req/s | **5,344** | 3,087 | 1.7× |
| HL7v2 ingest, peak msg/s | **1,120** | 324 | **3.5×** |
| **Reads per $1** | **~1.45 billion** | ~11.2 million | **~129×** |

The Mac mini didn't just win on cost — it won on **raw throughput**, while the cloud
GPU idled through every FHIR scenario (FHIR is CPU/DB-bound; you pay for silicon the
workload can't use). And the Mac was simultaneously running unrelated production
workloads during its runs.

**Honest caveats:** the pod's container reports the host's 128 cores but is sold as a
vCPU slice; the Mac's JVM was long-running (fully JIT-warmed) while the pod's was minutes
old (both got per-scenario warmup); single-run numbers, not averaged across many hosts.
The cost math survives all of these.

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
