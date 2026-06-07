#!/usr/bin/env bash
set -euo pipefail

# One-shot setup for fhirmini on an Apple-Silicon Mac. Idempotent — safe to re-run.
# Installs the toolchain, sets up native Postgres, builds the HAPI war, and creates the venv.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
HAPI_REPO="https://github.com/hapifhir/hapi-fhir-jpaserver-starter.git"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

# --- 0. sanity ---
[ "$(uname -s)" = "Darwin" ] || { echo "fhirmini targets macOS (Apple Silicon)."; exit 1; }
[ "$(uname -m)" = "arm64" ] || echo "WARNING: not arm64 — the MLX AI layer requires Apple Silicon."
command -v brew >/dev/null || { echo "Homebrew required: https://brew.sh"; exit 1; }

# --- 1. toolchain ---
say "Installing JDK 21 + Maven + Postgres 16 (Homebrew)"
brew list openjdk@21    >/dev/null 2>&1 || brew install openjdk@21
brew list maven         >/dev/null 2>&1 || brew install maven
brew list postgresql@16 >/dev/null 2>&1 || brew install postgresql@16
brew services start postgresql@16 >/dev/null 2>&1 || true
export JAVA_HOME="/opt/homebrew/opt/openjdk@21"
for i in $(seq 1 30); do pg_isready -q && break; sleep 1; done

# --- 2. .env ---
if [ ! -f .env ]; then
  say "Creating .env (generating a strong POSTGRES_PASSWORD)"
  cp .env.example .env
  PW="$(openssl rand -base64 24)"
  /usr/bin/sed -i '' "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PW}|" .env
  chmod 600 .env
else
  echo ".env already exists — leaving it."
fi
set -a; source .env; set +a

# --- 3. Postgres role + db + M4 tuning ---
say "Configuring Postgres role '${POSTGRES_USER}' + db '${POSTGRES_DB}'"
psql -d postgres -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='${POSTGRES_USER}') THEN
    CREATE ROLE ${POSTGRES_USER} LOGIN PASSWORD '${POSTGRES_PASSWORD}';
  ELSE ALTER ROLE ${POSTGRES_USER} PASSWORD '${POSTGRES_PASSWORD}'; END IF;
END \$\$;
SQL
psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${POSTGRES_DB}'" | grep -q 1 \
  || psql -d postgres -c "CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};"

PGDATA="$(psql -d postgres -tAc 'show data_directory;')"
mkdir -p "${PGDATA}/conf.d"
cat > "${PGDATA}/conf.d/10-fhirmini.conf" <<'CONF'
# fhirmini tuning for Apple-Silicon Mac (16GB). effective_io_concurrency MUST be 0 on macOS.
shared_buffers = 2GB
effective_cache_size = 4GB
work_mem = 32MB
maintenance_work_mem = 512MB
max_worker_processes = 10
max_parallel_workers = 8
max_parallel_workers_per_gather = 4
random_page_cost = 1.1
effective_io_concurrency = 0
wal_compression = on
jit = off
shared_preload_libraries = 'pg_stat_statements'
CONF
grep -q "include_dir = 'conf.d'" "${PGDATA}/postgresql.conf" \
  || printf "\ninclude_dir = 'conf.d'\n" >> "${PGDATA}/postgresql.conf"
brew services restart postgresql@16 >/dev/null 2>&1 || true
for i in $(seq 1 30); do pg_isready -q && break; sleep 1; done

# --- 4. build HAPI FHIR war ---
say "Building HAPI FHIR (this downloads a dependency tree on first run)"
mkdir -p .build
[ -d .build/hapi-fhir-jpaserver-starter ] || git clone --depth 1 "$HAPI_REPO" .build/hapi-fhir-jpaserver-starter
if [ ! -f .build/hapi-fhir-jpaserver-starter/target/ROOT.war ]; then
  ( cd .build/hapi-fhir-jpaserver-starter && JAVA_HOME="$JAVA_HOME" mvn -q -B clean package -DskipTests )
fi

# --- 5. python venv (reuse system MLX/torch) ---
say "Creating Python venv for the AI sidecar + engine"
[ -d fhir-ai/.venv ] || python3 -m venv --system-site-packages fhir-ai/.venv
fhir-ai/.venv/bin/python -m pip install -q -r fhir-ai/requirements.txt

say "Done. Start everything with:  scripts/fhirmini start   (then: scripts/fhirmini smoke)"
echo "Load synthetic data:  fhir-ai/.venv/bin/python scripts/load_sample_data.py 12 https://r4.smarthealthit.org"
