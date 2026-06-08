# fhirmini MCP server

Turns the whole fhirmini stack into [Model Context Protocol](https://modelcontextprotocol.io)
tools, so **any MCP client becomes the agent** that operates your FHIR server — Claude
Desktop/Code, or [picoclaw](https://github.com/sipeed/picoclaw) running on a $10 edge board.

```
  ┌─ MCP client / agent ─┐        MCP         ┌──────── fhirmini (Mac mini) ────────┐
  │ Claude Desktop/Code  │  stdio ──────────▶ │ mcp_server  ─┬─ FHIR repo  (:8080)  │
  │ picoclaw (edge board)│  http  ──────────▶ │             ├─ engine/MLLP(:8088/2575)
  └──────────────────────┘                    │             └─ MLX AI    (:8090)    │
                                               └─────────────────────────────────────┘
```

## Tools (14)

| Group | Tools |
|---|---|
| **lifecycle** | `stack_status`, `stack_start`, `stack_stop` |
| **FHIR** | `fhir_create_patient`, `fhir_create_observation`, `fhir_search`, `fhir_read`, `fhir_count` |
| **engine** | `hl7_send_adt`, `hl7_send_raw`, `engine_messages` |
| **AI** | `ai_search`, `ai_ask`, `ai_extract` |

Example agent turn: *"Register patient Jane Doe (MRN 5512), log a heart rate of 88, then tell
me if she has any cardiac risk."* → `hl7_send_adt` → `fhir_create_observation` → `ai_ask`
(cited from the resources it just created).

## Run it

```bash
scripts/run-mcp.sh                       # stdio  (Claude Desktop/Code, local picoclaw)
scripts/run-mcp.sh --http --port 8200    # streamable-HTTP (remote/edge clients)
```

## Connect — Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "fhirmini": { "command": "/ABS/PATH/TO/fhirmini/scripts/run-mcp.sh" }
  }
}
```
Restart Claude Desktop; the fhirmini tools appear. Now just ask it to register patients,
ingest HL7, search, or answer clinical questions.

## Connect — Claude Code

```bash
claude mcp add fhirmini /ABS/PATH/TO/fhirmini/scripts/run-mcp.sh
```

## Connect — picoclaw (the edge agent)

picoclaw is an MCP client (`picoclaw mcp add/list/test`). Two modes:

**Local** (picoclaw on the same Mac) — stdio:
```bash
picoclaw mcp add fhirmini -- /ABS/PATH/TO/fhirmini/scripts/run-mcp.sh
picoclaw mcp test fhirmini
```

**Edge** (picoclaw on a Sipeed/RISC-V board, fhirmini on the Mac) — HTTP over the network:
```bash
# on the Mac, expose the MCP server to the LAN (or, better, the tunnel — see security note):
scripts/run-mcp.sh --http --host 0.0.0.0 --port 8200
# on the board:
picoclaw mcp add fhirmini http://<mac-ip>:8200/mcp
```
picoclaw drives it with whatever tiny local or cloud LLM you've configured — a sub-$10
conversational healthcare front-end backed by your fast on-device FHIR/HL7/AI brain.

## ⚠️ Security

The HTTP transport has **no auth** by default and binds to **loopback** unless you pass
`--host 0.0.0.0`. For anything beyond a trusted LAN, do **not** expose it raw — put it behind
the **Cloudflare Tunnel + Access** (`scripts/setup-tunnel.sh`) so only authenticated clients
reach it. And remember: keep `PHASE=dev-sandbox` with synthetic data until the PHI gate is
deliberately cleared (see README).
