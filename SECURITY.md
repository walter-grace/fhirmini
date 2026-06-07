# Security Policy

## ⚠️ This project is for synthetic / development data

fhirmini is a **development-sandbox** FHIR platform. Out of the box it is **not** configured
for, and must **not** be used with, real Protected Health Information (PHI) or any production
patient data. Running real PHI is a legal and operational process — see the PHI section in the
README. The default posture (`PHASE=dev-sandbox`) and the AI layer's cloud-backend guard exist
to keep you on the synthetic-data path.

**Do not** open issues, pull requests, logs, or test fixtures that contain real patient data.
If you believe a contribution or artifact contains real PHI, report it privately (below) — do
not comment publicly.

## Reporting a vulnerability

Please report security vulnerabilities **privately**, not via public issues:

- Use GitHub's **"Report a vulnerability"** (Security Advisories) on this repository, **or**
- Email the maintainers (see the repository's profile / `CODENAME` contact).

Include: affected component (FHIR core / engine / AI sidecar), version or commit, reproduction
steps, and impact. We aim to acknowledge within a few business days.

## Scope & hardening notes

- The FHIR server and all services bind to **loopback (127.0.0.1)** by default; external
  exposure is intended only via an authenticated Cloudflare Tunnel + Access (see scripts).
- Secrets live in `.env` (gitignored). Never commit credentials.
- The integration engine disables referential-integrity-on-write by design (messages can
  arrive out of order) — treat ingested data as untrusted and validate downstream.
- The AI `local` backend keeps data on-device; the `openrouter` backend sends content to a
  third party and is hard-disabled when `PHASE=phi-readiness`.

## Supported versions

This is pre-1.0 software; security fixes are applied to the `main` branch.
