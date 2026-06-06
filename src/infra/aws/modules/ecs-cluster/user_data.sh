#!/bin/bash
set +e

# ----- 1. Configure and start ECS agent FIRST (critical) -----

cat <<EOF >> /etc/ecs/ecs.config
ECS_CLUSTER=${cluster_name}
ECS_ENABLE_MANAGED_INSTANCE=true
EOF

systemctl enable --now ecs

# ----- 2. Install cloudflared (best effort, does not block ECS) -----
(
  set +e
  # Change from 'yum' to 'dnf', dnf is the default package manager for Amazon Linux 2023
  dnf install -y curl 2>/dev/null || true
  
  curl -L "https://github.com/cloudflare/cloudflared/releases/download/2026.5.0/cloudflared-linux-arm64" -o /usr/local/bin/cloudflared
  if [ -f /usr/local/bin/cloudflared ]; then
    chmod +x /usr/local/bin/cloudflared
    # Added the --nowait flag for a more reliable install
    cloudflared service install "${cloudflare_tunnel_token}" --nowait 2>/dev/null || true
    
    mkdir -p /etc/cloudflared
    cat > /etc/cloudflared/config.yml << YAML
ingress:
  - hostname: ${cloudflare_hostname}
    path: ^/healthz$
    service: http_status:403
  - hostname: ${cloudflare_hostname}
    path: ^/readyz$
    service: http_status:403
  - hostname: ${cloudflare_hostname}
    path: /auth/*
    service: http://localhost:8000
  - hostname: ${cloudflare_hostname}
    path: /ws/*
    service: http://localhost:8000
  - hostname: ${cloudflare_hostname}
    path: /.well-known/*
    service: http://localhost:8000
  - hostname: ${cloudflare_hostname}
    path: /admin/*
    service: http://localhost:8000
  - hostname: ${cloudflare_hostname}
    path: /
    service: http://localhost:8000
  - service: http_status:404
YAML
    systemctl restart cloudflared 2>/dev/null || true
  fi
) &

echo "[INFO] ECS agent started. Cloudflared installation running in background."