#!/bin/bash
# =============================================================================
# AgentOps — EC2 User Data (Production, ARM64 / t4g)
# =============================================================================
# Runs once when an EC2 instance (Managed Instance) launches.
# - Downloads cloudflared binary for ARM64
# - Installs as systemd service using the official service installer
# - Writes config with selective ingress rules (mandatory catch‑all)
# - Starts the tunnel
#
# Required environment (injected by Terraform templatefile):
#   TUNNEL_TOKEN   – Cloudflare tunnel token (sensitive)
#   ECS_CLUSTER    – ECS cluster name
# =============================================================================
set -euo pipefail

# ── 1. Install cloudflared (ARM64) ─────────────────────────────────
CLOUDFLARED_VERSION="2026.5.0"
BIN_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64"
curl -L "${BIN_URL}" -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared

# ── 2. Install cloudflared as a systemd service with the token ──────
echo "[INFO] Installing cloudflared as systemd service..."
cloudflared service install "${TUNNEL_TOKEN}"

# ── 3. Write custom ingress config (overriding defaults) ────────────
mkdir -p /etc/cloudflared

cat > /etc/cloudflared/config.yml << 'YAML'
# Cloudflare Tunnel configuration
# Ingress rules are evaluated from top to bottom.
# The last rule MUST be a catch‑all rule.

ingress:
  # 🚫 BLOCK internal/administrative endpoints FIRST (exact path matches)
  - hostname: athithya.site
    path: ^/healthz$
    service: http_status:403
  - hostname: athithya.site
    path: ^/readyz$
    service: http_status:403
  - hostname: athithya.site
    path: /admin/*
    service: http_status:403
  - hostname: athithya.site
    path: /metrics
    service: http_status:403
  - hostname: athithya.site
    path: /debug/*
    service: http_status:403

  # ✅ Public routes (specific paths)
  - hostname: athithya.site
    path: /auth/*
    service: http://localhost:8000
  - hostname: athithya.site
    path: /ws/*
    service: http://localhost:8000
  - hostname: athithya.site
    path: /.well-known/*
    service: http://localhost:8000

  # 🌍 Root path (catch‑all for remaining valid requests)
  - hostname: athithya.site
    path: /
    service: http://localhost:8000

  # ⚠️ MANDATORY catch‑all for any unmatched host/path
  - service: http_status:404
YAML

# ── 4. Verify configuration before starting ─────────────────────────
echo "[INFO] Validating cloudflared configuration..."
cloudflared tunnel --config /etc/cloudflared/config.yml ingress rule https://athithya.site/ || true

# ── 5. Restart the service to pick up new config ────────────────────
echo "[INFO] Restarting cloudflared service with custom configuration..."
systemctl restart cloudflared

# ── 6. Verify service status ────────────────────────────────────────
echo "[INFO] Checking cloudflared service status..."
systemctl status cloudflared --no-pager || true

# ── 7. ECS agent configuration ─────────────────────────────────────
echo "ECS_CLUSTER=${ECS_CLUSTER}" >> /etc/ecs/ecs.config
echo "ECS_ENABLE_MANAGED_INSTANCE=true" >> /etc/ecs/ecs.config

echo "[INFO] Setup complete. Use 'sudo journalctl -u cloudflared -f' to monitor logs."