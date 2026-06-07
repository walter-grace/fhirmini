#!/usr/bin/env bash
set -euo pipefail

# Launch the healthcare integration engine: MLLP/TCP + HTTP + S3 inbound,
# HL7 v2 -> FHIR transform, routed to FHIR/S3/HTTP/file/MLLP destinations.
# Management API + HTTP inbound on :8088; MLLP listeners per channels.yaml.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f .env ] && set -a && source .env && set +a
export FHIR_BASE="${FHIR_BASE:-http://127.0.0.1:8080/fhir}"
export AI_URL="${AI_URL:-http://127.0.0.1:${AI_PORT:-8090}}"
mkdir -p "$ROOT/logs" "$ROOT/engine-out"

exec "$ROOT/fhir-ai/.venv/bin/python" -m uvicorn engine.server:app \
  --app-dir "$ROOT" \
  --host 127.0.0.1 \
  --port "${ENGINE_PORT:-8088}"
