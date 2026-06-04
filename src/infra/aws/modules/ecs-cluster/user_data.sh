#!/bin/bash
# =============================================================================
# AgentOps — EC2 User Data (Production, ARM64 / t4g)
# =============================================================================
set -euo pipefail

yum update -y ecs-init

# ----- 1. Install cloudflared (specific version for ARM64) -----
echo "[INFO] Installing cloudflared (2026.5.0) for ARM64..."
curl -L "https://github.com/cloudflare/cloudflared/releases/download/2026.5.0/cloudflared-linux-arm64" -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared

# ----- 2. Install cloudflared as a systemd service with the token -----
echo "[INFO] Installing cloudflared as systemd service..."
cloudflared service install "${cloudflare_tunnel_token}"

# ----- 3. Write custom ingress config to the correct location -----
mkdir -p /etc/cloudflared

# Important: No quotes around the delimiter 'YAML' to allow variable substitution
cat > /etc/cloudflared/config.yml << YAML
ingress:
  # Block internal endpoints
  - hostname: ${cloudflare_hostname}
    path: ^/healthz$
    service: http_status:403
  - hostname: ${cloudflare_hostname}
    path: ^/readyz$
    service: http_status:403

  # Auth + WebSocket + API
  - hostname: ${cloudflare_hostname}
    path: /auth/*
    service: http://localhost:8000
  - hostname: ${cloudflare_hostname}
    path: /ws/*
    service: http://localhost:8000
    originRequest:
      connectTimeout: "10s"
      keepAliveTimeout: "120s"
      disableChunkedEncoding: false
  - hostname: ${cloudflare_hostname}
    path: /.well-known/*
    service: http://localhost:8000

  # Admin dashboard + API
  - hostname: ${cloudflare_hostname}
    path: /admin/*
    service: http://localhost:8000

  # Catch‑all – all other requests go to agent service
  - service: http://localhost:8000
YAML

# ----- 4. Restart the service to pick up the new config -----
echo "[INFO] Restarting cloudflared service with custom configuration..."
systemctl restart cloudflared

# ----- 5. ECS agent configuration (as confirmed by AWS docs) -----
echo "ECS_CLUSTER=${cluster_name}" >> /etc/ecs/ecs.config
echo "ECS_ENABLE_MANAGED_INSTANCE=true" >> /etc/ecs/ecs.config

# Enable and start the ECS agent
systemctl enable --now ecs

echo "[INFO] Setup complete for instance in cluster ${cluster_name}."