#!/usr/bin/env bash
set -euo pipefail

# fhirmini MCP server launcher.
#   run-mcp.sh            -> stdio  (for Claude Desktop/Code, local picoclaw)
#   run-mcp.sh --http     -> streamable-HTTP on 127.0.0.1:8200 (for remote/edge picoclaw)
#   run-mcp.sh --http --host 0.0.0.0 --port 8200   (expose to LAN/tunnel — see docs/MCP.md)

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f .env ] && set -a && source .env && set +a

exec "$ROOT/fhir-ai/.venv/bin/python" -m mcp_server.server "$@"
