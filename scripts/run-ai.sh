#!/usr/bin/env bash
set -euo pipefail

# Launch the on-device AI sidecar (FastAPI). Embeddings run via MLX in-process.
# Generation uses AI_BACKEND from .env (local MLX server or OpenRouter).
# To serve a LOCAL MLX LLM for /ai/ask + /ai/extract, run scripts/run-ai-llm.sh too.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f .env ] && set -a && source .env && set +a

VENV="$ROOT/fhir-ai/.venv"
[ -x "$VENV/bin/uvicorn" ] || [ -x "$VENV/bin/python" ] || { echo "venv missing; create fhir-ai/.venv"; exit 1; }
mkdir -p "$ROOT/logs"

exec "$VENV/bin/python" -m uvicorn app:app \
  --app-dir "$ROOT/fhir-ai" \
  --host 127.0.0.1 \
  --port "${AI_PORT:-8090}"
