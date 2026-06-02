#!/bin/bash
# =============================================================================
# AgentOps — Local Cloudflared Tunnel Setup (Idempotent, No Login)
# =============================================================================
# Uses tunnel token + credentials file directly. No browser login required.
#
# Usage:
#   bash src/offline/cloudflared_setup.sh
#
# Stop:
#   pkill -f "cloudflared tunnel run"
# =============================================================================
set -euo pipefail

DOMAIN="${DOMAIN:-athithya.site}"
AGENT_PORT="${AGENT_PORT:-8000}"
CONFIG_DIR="${HOME}/.cloudflared"
CONFIG_FILE="${CONFIG_DIR}/agentops-tunnel.yml"

# ── 0. Kill any existing cloudflared process ────────────────────────
if pgrep -f "cloudflared tunnel run" >/dev/null 2>&1; then
  echo "[INFO] Stopping existing cloudflared process..."
  pkill -f "cloudflared tunnel run" 2>/dev/null || true
  sleep 1
fi

# ── 1. Verify agent-service is reachable ───────────────────────────
echo "[INFO] Checking agent-service on http://localhost:${AGENT_PORT}..."
if ! curl -sf --max-time 2 "http://localhost:${AGENT_PORT}/healthz" >/dev/null 2>&1; then
  echo "[ERROR] Agent service is NOT reachable."
  echo "        Start it first: bash src/offline/commands.sh"
  exit 1
fi
echo "[INFO] Agent service is reachable."

# ── 2. Fetch tunnel credentials from OpenTofu outputs ──────────────
TUNNEL_ID="$(tofu -chdir=src/infra/cloudflare output -raw cloudflare_tunnel_id 2>/dev/null || echo '')"
TUNNEL_TOKEN="$(tofu -chdir=src/infra/cloudflare output -raw cloudflare_tunnel_token 2>/dev/null || echo '')"

if [[ -z "${TUNNEL_ID}" || "${TUNNEL_ID}" == "null" ]]; then
  echo "[ERROR] No tunnel found in OpenTofu state."
  echo "        Create it first:  bash src/infra/cloudflare/run.sh --apply"
  exit 1
fi

echo "[INFO] Tunnel ID: ${TUNNEL_ID}"

# ── 3. Ensure credentials file exists (idempotent) ─────────────────
CRED_FILE="${CONFIG_DIR}/${TUNNEL_ID}.json"
if [[ ! -f "${CRED_FILE}" ]]; then
  echo "[INFO] Credentials file missing. Creating from Cloudflare API..."
  
  : "${CLOUDFLARE_ACCOUNT_ID:?export CLOUDFLARE_ACCOUNT_ID first}"

  # Fetch tunnel token JSON and write to credentials file
  curl -sf -X GET \
    "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel/${TUNNEL_ID}/token" \
    -H "Authorization: Bearer ${CLOUDFLARE_GLOBAL_API_KEY}" \
    -H "Content-Type: application/json" \
    -o "${CRED_FILE}" || {
      echo "[WARN] API fetch failed. Creating minimal cred file."
      echo "{\"AccountTag\":\"${CLOUDFLARE_ACCOUNT_ID}\",\"TunnelID\":\"${TUNNEL_ID}\",\"TunnelName\":\"agentops-tunnel\"}" > "${CRED_FILE}"
    }
  echo "[INFO] Credentials written to ${CRED_FILE}"
else
  echo "[INFO] Credentials file already exists: ${CRED_FILE}"
fi

# ── 4. Write cloudflared config (idempotent — overwrites) ──────────
mkdir -p "${CONFIG_DIR}"

cat > "${CONFIG_FILE}" << YAML
tunnel: ${TUNNEL_ID}
credentials-file: ${CRED_FILE}
no-autoupdate: true

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
      
  # Admin dashboard + API
  - hostname: ${DOMAIN}
    path: /admin/*
    service: http://localhost:${AGENT_PORT}

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

  # Catch-all: reject everything else
  - service: http_status:404
YAML

echo "[INFO] Config written to ${CONFIG_FILE}"

# ── 5. Start cloudflared with the existing tunnel ──────────────────
export TUNNEL_TOKEN

echo "[INFO] Starting cloudflared → https://${DOMAIN} → localhost:${AGENT_PORT}"
echo "[INFO] Logs: /tmp/cloudflared.log"
echo "[INFO] To stop: pkill -f 'cloudflared tunnel run'"
echo ""

nohup cloudflared tunnel --config "${CONFIG_FILE}" run "${TUNNEL_ID}" \
  > /tmp/cloudflared.log 2>&1 &
CLOUDFLARED_PID=$!

sleep 4

# Check both PID file and actual process
if kill -0 "${CLOUDFLARED_PID}" 2>/dev/null; then
  echo "[INFO] Tunnel is running (PID ${CLOUDFLARED_PID})"
elif pgrep -f "cloudflared.*tunnel.*run" >/dev/null 2>&1; then
  echo "[INFO] Tunnel is running (found via pgrep)"
else
  # Check if logs show successful registration
  if grep -q "Registered tunnel connection" /tmp/cloudflared.log 2>/dev/null; then
    echo "[INFO] Tunnel appears to be running (connections registered in log)"
  else
    echo "[ERROR] Tunnel may have failed. Last 10 lines:"
    tail -10 /tmp/cloudflared.log
    exit 1
  fi
fi

echo "[INFO] Visit: https://${DOMAIN}"