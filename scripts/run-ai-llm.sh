#!/usr/bin/env bash
set -euo pipefail

# Serve the LOCAL on-device LLM (PHI-safe generation) via mlx-vlm's OpenAI-compatible
# server. Reuses the already-downloaded Qwen2.5-VL-7B-Instruct-4bit (text-only prompts
# work fine). Listens on :8081 — the AI sidecar's AI_LOCAL_LLM_URL points here.
#
# NOTE: loads ~4.3GB into unified memory. Run only when you need /ai/ask or /ai/extract
# with AI_BACKEND=local. For synthetic-data dev you can instead set AI_BACKEND=openrouter.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f .env ] && set -a && source .env && set +a

MODEL="${AI_LOCAL_LLM:-mlx-community/Qwen2.5-VL-7B-Instruct-4bit}"
PORT="$(echo "${AI_LOCAL_LLM_URL:-http://127.0.0.1:8081/v1}" | sed -E 's#.*:([0-9]+).*#\1#')"
mkdir -p "$ROOT/logs"

exec "$ROOT/fhir-ai/.venv/bin/python" -m mlx_vlm.server \
  --model "$MODEL" \
  --host 127.0.0.1 \
  --port "$PORT"
