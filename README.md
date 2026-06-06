# Mac FHIR Server

A self-hosted **HAPI FHIR** R4 server on a Mac Mini (Apple Silicon), backed by
**PostgreSQL 16**, exposed through a **Cloudflare Tunnel** with **Zero Trust**
authentication. No inbound ports are opened on your network.

## Architecture

```
Internet ─▶ Cloudflare Edge (Access / IdP auth + MFA)
                │  encrypted, outbound-only tunnel
                ▼
        cloudflared  (launchd service)
                │  http://localhost:8080
                ▼
   Docker Desktop (linux/arm64)
     hapi-fhir (8080) ──▶ postgres:16 (5432, internal network only)
                              │
                          named volume  (hapi-pgdata)
```

Design choices:
- HAPI binds to `127.0.0.1:8080` only — the tunnel is the *sole* external path.
- Postgres publishes **no** host port in exposed/production phases.
- Cloudflare Access sits in front; the endpoint is never open to the public internet.
- Images are version-pinned for reproducibility.

## Quick start (dev-sandbox)

```bash
cp .env.example .env
# edit .env, set a strong POSTGRES_PASSWORD:  openssl rand -base64 24
docker compose up -d
curl http://localhost:8080/fhir/metadata        # should return a capability statement
```

Hand the file `docs/CLAUDE_CODE_PROMPT.md` to Claude Code to drive the build.

## Roadmap (phased — see CLAUDE.md for the gates)

1. **dev-sandbox** — local only, synthetic data, working API.
2. **tunnel-exposed** — `scripts/setup-tunnel.sh` + Cloudflare Access, still synthetic data.
3. **hardening** — scheduled `scripts/backup-db.sh`, audit-logging interceptor, secrets
   hygiene, FileVault, Prometheus/health monitoring, log rotation.
4. **phi-readiness** — compliance gate. Do not enter without deliberate review.

## ⚠️ Before you ever put real patient data in this

This repo gets you a solid *technical* foundation, but running real PHI is a legal and
operational process, not a flag you flip. At minimum:

- **Cloudflare BAA is Enterprise-only and service-scoped.** The free tunnel is **not** a
  PHI-eligible path. You need an Enterprise agreement with a signed BAA that explicitly
  names the services carrying your traffic. Confirm current terms with Cloudflare directly.
- **Encryption at rest:** enable FileVault on macOS; consider encrypted volumes for backups.
- **Physical safeguards:** HIPAA requires them. A Mac Mini on a desk is a theft risk —
  locked rack or access-controlled room.
- **Audit logging:** configure HAPI's audit interceptors to record who accessed which
  resource and when. Ship logs somewhere tamper-resistant.
- **Risk analysis & policies:** breach notification, access reviews, retention/backup
  schedules, and a documented risk assessment.
- **Get qualified advice.** This is not legal advice; a compliance professional should
  review your specific setup before any PHI touches it.

A single Mac Mini behind a tunnel is excellent for development, demos, research with
synthetic data, and internal interoperability prototyping. Treat the production-PHI path
as a separate, deliberate project.

## Files
- `docker-compose.yml` — HAPI FHIR + Postgres
- `.env.example` — secrets template (copy to `.env`)
- `scripts/setup-tunnel.sh` — provisions the Cloudflare named tunnel
- `scripts/backup-db.sh` — logical Postgres backups with rotation
- `docs/CLAUDE_CODE_PROMPT.md` — kickoff prompt for Claude Code
- `CLAUDE.md` — persistent guardrails Claude Code reads each turn
