#!/usr/bin/env bash
set -euo pipefail

# Sets up a Cloudflare named tunnel for the local HAPI FHIR server.
# Prereqs: Homebrew, a Cloudflare account, a domain on Cloudflare.
# Run AFTER the docker stack is up and http://localhost:8080/fhir/metadata responds.

[ -f .env ] && set -a && source .env && set +a

: "${TUNNEL_NAME:?set TUNNEL_NAME in .env}"
: "${TUNNEL_HOSTNAME:?set TUNNEL_HOSTNAME in .env}"

echo "==> Installing cloudflared (if needed)"
command -v cloudflared >/dev/null 2>&1 || brew install cloudflare/cloudflare/cloudflared

echo "==> Authenticating (opens a browser; pick the zone for ${TUNNEL_HOSTNAME})"
cloudflared tunnel login

echo "==> Creating tunnel '${TUNNEL_NAME}' (skips if it exists)"
cloudflared tunnel create "${TUNNEL_NAME}" 2>/dev/null || echo "    tunnel may already exist; continuing"

TUNNEL_ID="$(cloudflared tunnel list | awk -v n="${TUNNEL_NAME}" '$2==n {print $1}')"
echo "    tunnel id: ${TUNNEL_ID}"

echo "==> Writing ~/.cloudflared/config.yml"
mkdir -p "${HOME}/.cloudflared"
cat > "${HOME}/.cloudflared/config.yml" <<EOF
tunnel: ${TUNNEL_ID}
credentials-file: ${HOME}/.cloudflared/${TUNNEL_ID}.json

ingress:
  - hostname: ${TUNNEL_HOSTNAME}
    service: http://localhost:8080
  # everything else is rejected
  - service: http_status:404
EOF

echo "==> Routing DNS ${TUNNEL_HOSTNAME} -> tunnel"
cloudflared tunnel route dns "${TUNNEL_NAME}" "${TUNNEL_HOSTNAME}"

echo "==> Installing as a launchd service (starts at login)"
sudo cloudflared service install || true

cat <<EOF

Done. Next steps (manual, in the Cloudflare Zero Trust dashboard):
  1. Access > Applications > Add > Self-hosted
     - Application domain: ${TUNNEL_HOSTNAME}
     - Add an identity provider (Google / GitHub / Email OTP)
     - Policy: Allow only your email(s); require MFA
  2. Verify in an incognito window: https://${TUNNEL_HOSTNAME}/fhir/metadata
     should hit the Access login BEFORE reaching the server.

Until Access is configured, your FHIR endpoint is PUBLIC. Do step 1 now.
EOF
