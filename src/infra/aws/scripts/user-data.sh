#!/bin/bash
# Runs once when EC2 instance launches
# Production: pinned version, ARM64, systemd service
set -euo pipefail

# 1. Download pinned binary
CLOUDFLARED_VERSION="2026.5.0"          # ← update this manually when you want to upgrade
BIN_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
curl -L "${BIN_URL}" -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared

# 2. Install as systemd service (token injected via Terraform)
# This auto-generates /etc/cloudflared/config.yml with origin = localhost:8000
cloudflared service install "${TUNNEL_TOKEN}"

# 3. Start the service
systemctl enable cloudflared
systemctl start cloudflared