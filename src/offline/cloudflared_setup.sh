#!/bin/bash
# =============================================================================
# AgentOps — Local Cloudflared Tunnel Setup (YAML Config, Reproducible)
# =============================================================================
# Prerequisites:
#   1. CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_GLOBAL_API_KEY exported
#   2. OpenTofu installed (tofu)
#   3. cloudflared installed
#   4. agent-service running on http://localhost:8000
#
# Usage:
#   export CLOUDFLARE_ACCOUNT_ID=xxx
#   export CLOUDFLARE_GLOBAL_API_KEY=xxx
#   bash src/offline/cloudflared_setup.sh
# =============================================================================
set -euo pipefail

: "${CLOUDFLARE_ACCOUNT_ID:?export CLOUDFLARE_ACCOUNT_ID first}"
: "${CLOUDFLARE_GLOBAL_API_KEY:?export CLOUDFLARE_GLOBAL_API_KEY first}"
export CLOUDFLARE_EMAIL="${CLOUDFLARE_EMAIL:-athithya651@gmail.com}"
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

# ── 2. Create/refresh Cloudflare resources (idempotent) ────────────
echo "[INFO] Applying Cloudflare configuration..."
bash src/infra/cloudflare/run.sh --apply

# ── 3. Fetch tunnel token and ID from Terraform outputs ────────────
TUNNEL_TOKEN="$(tofu -chdir=src/infra/cloudflare output -raw cloudflare_tunnel_token 2>/dev/null)"
TUNNEL_ID="$(tofu -chdir=src/infra/cloudflare output -raw cloudflare_tunnel_id 2>/dev/null)"

if [[ -z "${TUNNEL_TOKEN}" || "${TUNNEL_TOKEN}" == "null" ]]; then
  echo "[ERROR] Could not read tunnel token from Terraform."
  exit 1
fi

echo "[INFO] Tunnel ID: ${TUNNEL_ID}"

# ── 4. Write cloudflared config with selective ingress ─────────────
mkdir -p ~/.cloudflared

cat > ~/.cloudflared/agentops-tunnel.yml << YAML
# tunnel: ${TUNNEL_ID}   # Optional if provided in 'run' command

ingress:
  # 🚫 BLOCK internal/administrative endpoints (must come before '/' rule)
  - hostname: ${DOMAIN}
    path: ^/healthz$      # Exact match only
    service: http_status:403
  - hostname: ${DOMAIN}
    path: ^/readyz$
    service: http_status:403
  - hostname: ${DOMAIN}
    path: /admin/*
    service: http_status:403

  # ✅ Public routes (specific paths)
  - hostname: ${DOMAIN}
    path: /auth/*
    service: http://localhost:${AGENT_PORT}
  - hostname: ${DOMAIN}
    path: /ws/*
    service: http://localhost:${AGENT_PORT}
  - hostname: ${DOMAIN}
    path: /.well-known/*
    service: http://localhost:${AGENT_PORT}

  # 🌍 Root path (catch-all for remaining valid requests)
  - hostname: ${DOMAIN}
    path: /
    service: http://localhost:${AGENT_PORT}

  # ⚠️ MANDATORY catch-all for any unmatched host/path
  - service: http_status:404
YAML

echo "[INFO] Config written to ~/.cloudflared/agentops-tunnel.yml"

# ── 5. Start cloudflared using the environment variable method ──────
echo "[INFO] Starting cloudflared tunnel → https://${DOMAIN} → localhost:${AGENT_PORT}"
export TUNNEL_TOKEN="${TUNNEL_TOKEN}"
cloudflared tunnel --config ~/.cloudflared/agentops-tunnel.yml run "${TUNNEL_ID}"