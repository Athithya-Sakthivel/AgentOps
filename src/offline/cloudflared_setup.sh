#!/bin/bash
# =============================================================================
# AgentOps — Local Cloudflared Tunnel Setup (Run Existing Tunnel)
# =============================================================================
# Prerequisites:
#   1. Cloudflare resources already created via OpenTofu (src/infra/cloudflare)
#   2. cloudflared installed
#   3. agent-service running on http://localhost:8000
#
# Usage:
#   bash src/offline/cloudflared_setup.sh
# =============================================================================
set -euo pipefail

DOMAIN="${DOMAIN:-athithya.site}"
AGENT_PORT="${AGENT_PORT:-8000}"

# ── 1. Verify agent-service is reachable ───────────────────────────
echo "[INFO] Checking agent-service on http://localhost:${AGENT_PORT}..."
if ! curl -sf --max-time 2 "http://localhost:${AGENT_PORT}/healthz" >/dev/null 2>&1; then
  echo "[ERROR] Agent service is NOT reachable on http://localhost:${AGENT_PORT}"
  echo "        Start it first (see src/offline/commands.sh)."
  exit 1
fi
echo "[INFO] Agent service is reachable."

# ── 2. Fetch tunnel credentials from OpenTofu outputs ──────────────
TUNNEL_ID="$(tofu -chdir=src/infra/cloudflare output -raw cloudflare_tunnel_id 2>/dev/null)"
TUNNEL_TOKEN="$(tofu -chdir=src/infra/cloudflare output -raw cloudflare_tunnel_token 2>/dev/null)"

if [[ -z "${TUNNEL_ID}" || "${TUNNEL_ID}" == "null" ]]; then
  echo "[ERROR] Could not read tunnel ID from OpenTofu."
  echo "        Run 'bash src/infra/cloudflare/run.sh --apply' first."
  exit 1
fi

if [[ -z "${TUNNEL_TOKEN}" || "${TUNNEL_TOKEN}" == "null" ]]; then
  echo "[ERROR] Could not read tunnel token from OpenTofu."
  exit 1
fi

echo "[INFO] Tunnel ID: ${TUNNEL_ID}"

# ── 3. Login to cloudflared (one-time, stores cert in ~/.cloudflared) ──
echo "[INFO] Authenticating cloudflared..."
cloudflared tunnel login --no-open-browser 2>/dev/null || true

# ── 4. Write cloudflared config with selective ingress ─────────────
mkdir -p ~/.cloudflared

cat > ~/.cloudflared/agentops-tunnel.yml << YAML
tunnel: ${TUNNEL_ID}
credentials-file: /root/.cloudflared/${TUNNEL_ID}.json

ingress:
  # Block internal health endpoints from public access
  - hostname: ${DOMAIN}
    path: ^/healthz$
    service: http_status:403
  - hostname: ${DOMAIN}
    path: ^/readyz$
    service: http_status:403

  # Auth endpoints
  - hostname: ${DOMAIN}
    path: /auth/*
    service: http://localhost:${AGENT_PORT}

  # WebSocket chat
  - hostname: ${DOMAIN}
    path: /ws/*
    service: http://localhost:${AGENT_PORT}
    originRequest:
      connectTimeout: "10s"
      keepAliveTimeout: "120s"
      disableChunkedEncoding: false

  # JWKS endpoint
  - hostname: ${DOMAIN}
    path: /.well-known/*
    service: http://localhost:${AGENT_PORT}

  # Frontend (root)
  - hostname: ${DOMAIN}
    path: /
    service: http://localhost:${AGENT_PORT}
    originRequest:
      connectTimeout: "10s"
      keepAliveTimeout: "60s"
      disableChunkedEncoding: false

  # Catch-all: reject everything else
  - service: http_status:404
YAML

echo "[INFO] Config written to ~/.cloudflared/agentops-tunnel.yml"

# ── 5. Start cloudflared with the existing tunnel ──────────────────
echo "[INFO] Starting cloudflared tunnel → https://${DOMAIN} → localhost:${AGENT_PORT}"
echo "[INFO] Press Ctrl+C to stop."

export TUNNEL_TOKEN
cloudflared tunnel run --config ~/.cloudflared/agentops-tunnel.yml "${TUNNEL_ID}"