# Claude Code kickoff prompt

Paste the block below into Claude Code from inside the project directory (the one
containing CLAUDE.md). It assumes the skeleton files are already present.

---

You are helping me build a self-hosted FHIR server on a Mac Mini (Apple Silicon),
exposed via Cloudflare Tunnel with Zero Trust auth. Read CLAUDE.md first and follow
every HARD RULE in it. We are in PHASE: dev-sandbox.

Goal for this session: get a working local FHIR server with synthetic data.

Do these in order, pausing after each numbered step so I can confirm before you continue:

1. Verify my environment: check that Docker Desktop is running and `docker compose version`
   reports v2. Tell me what to install if anything is missing (don't install it yourself).

2. Walk me through creating my .env from .env.example, including generating a strong
   POSTGRES_PASSWORD with openssl. Do not print or commit the real value.

3. Bring up the stack with `docker compose up -d`, then poll
   http://localhost:8080/fhir/metadata until it returns a 200 capability statement.
   If it errors, diagnose from the container logs.

4. Load synthetic test data: create 3 example Patient resources and 1 Observation
   linked to one of them, via POST to /fhir. Then show me a GET /fhir/Patient proving
   they persisted. Use clearly fake names (e.g., "Test Patient Alpha").

5. Confirm persistence survives a restart: `docker compose restart`, then re-query.

Stop after step 5 and summarize what's running and what the next phase (tunnel-exposed)
will require. Do NOT touch cloudflared or anything internet-facing this session.

If any step needs a destructive command or seems to require real patient data, stop and
ask me.
