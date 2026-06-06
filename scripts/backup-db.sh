#!/usr/bin/env bash
set -euo pipefail

# Logical backup of the HAPI Postgres database via pg_dump inside the container.
# Schedule with launchd or cron. Keeps the last 14 dumps.

[ -f .env ] && set -a && source .env && set +a
: "${POSTGRES_USER:?}"; : "${POSTGRES_DB:?}"

BACKUP_DIR="./backups"
mkdir -p "${BACKUP_DIR}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${BACKUP_DIR}/hapi-${STAMP}.sql.gz"

echo "==> Dumping ${POSTGRES_DB} -> ${OUT}"
docker exec hapi-postgres pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" | gzip > "${OUT}"

echo "==> Pruning old backups (keeping last 14)"
ls -1t "${BACKUP_DIR}"/hapi-*.sql.gz | tail -n +15 | xargs -r rm --

echo "==> Done. Current backups:"
ls -lh "${BACKUP_DIR}"/hapi-*.sql.gz
