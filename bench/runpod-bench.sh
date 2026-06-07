#!/usr/bin/env bash
set -euxo pipefail

# Fully-unattended fhirmini benchmark for a RunPod (Ubuntu/CUDA) container.
# Installs the IDENTICAL stack benchmarked on the Mac mini (Temurin 21, Postgres 16,
# HAPI starter @ the same git SHA, same application.yaml, same harness), then serves
# results on :8000 (expose via RunPod's HTTP proxy).
#
# Pod start command example:
#   bash -c "apt-get update && apt-get install -y git && \
#            git clone https://github.com/walter-grace/fhirmini /workspace/fhirmini && \
#            COST_PER_HOUR=0.40 LABEL=runpod-4090 bash /workspace/fhirmini/bench/runpod-bench.sh"

HAPI_SHA="${HAPI_SHA:-8bf8a75c8c87df3ba7b470797ba0ef6dce9e4baf}"
LABEL="${LABEL:-runpod}"
COST_PER_HOUR="${COST_PER_HOUR:-0.40}"
REPO_DIR="${REPO_DIR:-/workspace/fhirmini}"
OUT=/workspace/out
mkdir -p "$OUT"
exec > >(tee "$OUT/setup.log") 2>&1

# observability first: serve $OUT immediately so progress is visible via the proxy
(cd "$OUT" && nohup python3 -m http.server 8000 >/dev/null 2>&1 &)

# CRITICAL: on ANY failure, keep the pod alive serving its own post-mortem instead of
# exiting (an exiting container restart-loops and destroys the evidence).
trap 'code=$?; echo "FAILED line $LINENO (exit $code)" | tee "$OUT/FAILED"; sleep infinity' ERR

# idempotency: a restarted container must not trip over a previous attempt
rm -rf /workspace/hapi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y curl git gnupg lsb-release sudo procps

# Maven: apt on ubuntu 22.04 ships 3.6.3 but the HAPI starter enforces >=3.8.3 — use the tarball
MVN_VER=3.9.9
curl -fsSL "https://dlcdn.apache.org/maven/maven-3/${MVN_VER}/binaries/apache-maven-${MVN_VER}-bin.tar.gz" -o /tmp/maven.tgz \
  || curl -fsSL "https://archive.apache.org/dist/maven/maven-3/${MVN_VER}/binaries/apache-maven-${MVN_VER}-bin.tar.gz" -o /tmp/maven.tgz
tar xzf /tmp/maven.tgz -C /opt
export PATH="/opt/apache-maven-${MVN_VER}/bin:$PATH"

# --- Temurin 21 + PostgreSQL 16 (vendor repos work on 20.04/22.04/24.04) ---
mkdir -p /etc/apt/keyrings
curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public \
  | gpg --dearmor -o /etc/apt/keyrings/adoptium.gpg
echo "deb [signed-by=/etc/apt/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb $(lsb_release -cs) main" \
  > /etc/apt/sources.list.d/adoptium.list
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
  | gpg --dearmor -o /etc/apt/keyrings/pgdg.gpg
echo "deb [signed-by=/etc/apt/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
  > /etc/apt/sources.list.d/pgdg.list
apt-get update
apt-get install -y temurin-21-jdk postgresql-16
export JAVA_HOME=/usr/lib/jvm/temurin-21-jdk-amd64

# --- Postgres: same tuning as the Mac (linux gets real effective_io_concurrency) ---
PGCONF=/etc/postgresql/16/main/conf.d/10-fhirmini.conf
cat > "$PGCONF" <<'CONF'
shared_buffers = 2GB
effective_cache_size = 4GB
work_mem = 32MB
maintenance_work_mem = 512MB
max_parallel_workers = 8
max_parallel_workers_per_gather = 4
random_page_cost = 1.1
effective_io_concurrency = 256
wal_compression = on
jit = off
shared_preload_libraries = 'pg_stat_statements'
CONF
pg_ctlcluster 16 main start || service postgresql start
sudo -u postgres psql -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='hapi_admin') THEN CREATE ROLE hapi_admin LOGIN PASSWORD 'bench'; END IF; END \$\$;"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='hapi'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE DATABASE hapi OWNER hapi_admin;"
pg_ctlcluster 16 main restart

# --- Build HAPI at the SAME SHA as the Mac baseline ---
git clone https://github.com/hapifhir/hapi-fhir-jpaserver-starter /workspace/hapi
cd /workspace/hapi && git checkout "$HAPI_SHA"
mvn -q -B clean package -DskipTests

# --- Run HAPI with the SAME config + JVM flags ---
cd "$REPO_DIR"
mkdir -p logs
export DB_PASSWORD=bench
nohup "$JAVA_HOME/bin/java" \
  -XX:+UseZGC -XX:+ZGenerational -Xms1g -Xmx2g -XX:+UseStringDeduplication \
  -jar /workspace/hapi/target/ROOT.war \
  --spring.config.additional-location="file:$REPO_DIR/config/application.yaml" \
  > logs/hapi.out 2> logs/hapi.err &
for i in $(seq 1 120); do
  curl -sf http://127.0.0.1:8080/fhir/metadata >/dev/null && break; sleep 2
done

# --- Integration engine (for the MLLP scenario) ---
pip install -q fastapi uvicorn "psycopg[binary]" hl7 pyyaml numpy
export POSTGRES_DB=hapi POSTGRES_USER=hapi_admin POSTGRES_PASSWORD=bench
export FHIR_BASE=http://127.0.0.1:8080/fhir
nohup python3 -m uvicorn engine.server:app --app-dir "$REPO_DIR" \
  --host 127.0.0.1 --port 8088 > logs/engine.out 2> logs/engine.err &
for i in $(seq 1 30); do curl -sf http://127.0.0.1:8088/engine/health >/dev/null && break; sleep 1; done

# --- Same synthetic dataset ---
python3 scripts/load_sample_data.py 12 https://r4.smarthealthit.org | tee "$OUT/load.log"

# --- System info for the report ---
python3 - <<PY > "$OUT/sysinfo.json"
import json, os, subprocess
def sh(c):
    try: return subprocess.check_output(c, shell=True, text=True).strip()
    except Exception: return ""
print(json.dumps({
  "cpus": os.cpu_count(),
  "cpu_model": sh("grep -m1 'model name' /proc/cpuinfo | cut -d: -f2"),
  "mem_gb": round(int(sh("grep MemTotal /proc/meminfo | grep -o '[0-9]*'") or 0)/1048576, 1),
  "gpu": sh("nvidia-smi --query-gpu=name --format=csv,noheader | head -1"),
}, indent=2))
PY

# --- Benchmark (identical invocation to the Mac baseline) ---
python3 bench/fhir_bench.py \
  --base http://127.0.0.1:8080/fhir \
  --label "$LABEL" \
  --concurrency 1,8,32 \
  --seconds 6 \
  --mllp 127.0.0.1:2575 \
  --cost-per-hour "$COST_PER_HOUR" \
  --out "$OUT/results.json" | tee "$OUT/bench.txt"

echo done > "$OUT/DONE"
echo "BENCH COMPLETE — results at :8000/results.json"
sleep infinity
