#!/usr/bin/env bash
set -euo pipefail

# Logical backup of the HAPI Postgres database via NATIVE pg_dump (no Docker).
# Schedule with launchd or cron. Keeps the last 14 dumps.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f .env ] && set -a && source .env && set +a
: "${POSTGRES_USER:?set POSTGRES_USER in .env}"; : "${POSTGRES_DB:?set POSTGRES_DB in .env}"

export PGHOST="${PGHOST:-127.0.0.1}" PGPORT="${PGPORT:-5432}"
export PGPASSWORD="${POSTGRES_PASSWORD:-}"

BACKUP_DIR="${ROOT}/backups"
mkdir -p "${BACKUP_DIR}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${BACKUP_DIR}/hapi-${STAMP}.sql.gz"

echo "==> Dumping ${POSTGRES_DB} (native pg_dump) -> ${OUT}"
# -Fc would be smaller/parallel-restorable; plain SQL keeps it greppable + portable.
pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" | gzip > "${OUT}"

echo "==> Pruning old backups (keeping last 14)"
ls -1t "${BACKUP_DIR}"/hapi-*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm --

echo "==> Done. Current backups:"
ls -lh "${BACKUP_DIR}"/hapi-*.sql.gz

# daily maintenance: rotate logs too
"${ROOT}/scripts/rotate-logs.sh" || true
