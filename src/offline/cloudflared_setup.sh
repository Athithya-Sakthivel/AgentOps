#!/bin/bash
# AgentOps cloudflared setup — mimics EC2 user-data: creates tunnel, writes config, starts foreground process
set -euo pipefail
mkdir -p ~/.cloudflared

# -- Credentials must be exported in your shell before running this script --
: "${CLOUDFLARE_ACCOUNT_ID:?export CLOUDFLARE_ACCOUNT_ID first}"
: "${CLOUDFLARE_GLOBAL_API_KEY:?export CLOUDFLARE_GLOBAL_API_KEY first}"
export CLOUDFLARE_EMAIL="${CLOUDFLARE_EMAIL:-athithya651@gmail.com}"
export DOMAIN="${DOMAIN:-athithya.site}"

# Apply Terraform (creates DNS records, zone settings)
bash src/infra/cloudflare/run.sh --apply

# Fetch tunnel token and ID from Terraform
CLOUDFLARE_TUNNEL_TOKEN="$(tofu -chdir=src/infra/cloudflare output -raw cloudflare_tunnel_token 2>/dev/null)"
TUNNEL_ID="$(tofu -chdir=src/infra/cloudflare output -raw cloudflare_tunnel_id 2>/dev/null)"
echo "Tunnel ID: ${TUNNEL_ID}"

# Write ingress config (no credentials file needed — we use --token)
cat > ~/.cloudflared/agentops-tunnel.yml << YAML
tunnel: ${TUNNEL_ID}
ingress:
  - hostname: athithya.site
    service: http://localhost:8000
  - hostname: "*.athithya.site"
    service: http://localhost:8000
  - service: http_status:404
YAML

echo "Config written to ~/.cloudflared/agentops-tunnel.yml"

# Start the tunnel using --token (bypasses credentials file entirely)
echo "Starting cloudflared tunnel (foreground)..."
cloudflared tunnel run --token "${CLOUDFLARE_TUNNEL_TOKEN}" "${TUNNEL_ID}"