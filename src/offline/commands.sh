#!/bin/bash
set -euo pipefail

# =============================================================================
# AgentOps — Local Startup Commands
# =============================================================================
# IMPORTANT: Secrets MUST come from SSM, not environment variables.
# Unset them before starting agent-service to catch SSM config bugs early.
# =============================================================================

curl -s https://athithya.site/auth/logout -c /tmp/cookies.txt -b /tmp/cookies.txt || true

# Kill any process on port 8000 (don't fail if none)
echo "Cleaning up port 8000..."
lsof -ti :8000 | xargs kill -9 2>/dev/null || true
lsof -ti :8001 | xargs kill -9 2>/dev/null || true
sleep 2
lsof -ti :8000 && echo "Port 8000 STILL IN USE" && exit 1 || echo "Port 8000 free"

PGPASSWORD=localdev psql -h localhost -p 5432 -U agentops -d kestral -c "DELETE FROM tickets WHERE created_at > NOW() - INTERVAL '1 day';"

# ── Start MCP Server (no AWS secrets needed) ──────────────────────
echo "[1/3] Starting mcp-server..."
cd /workspace/src/workloads/mcp-server
source .venv/bin/activate
export PORT=8001
export DATABASE_URL="postgresql://agentops:localdev@localhost:5432/kestral"
python3 src/main.py > /tmp/mcp-server.log 2>&1 &

# ── Unset ALL SSM-managed secrets ─────────────────────────────────
echo "[2/3] Unsetting SSM-managed env vars..."
unset GOOGLE_CLIENT_ID
unset GOOGLE_CLIENT_SECRET
unset MICROSOFT_CLIENT_ID
unset MICROSOFT_CLIENT_SECRET
unset MICROSOFT_TENANT_ID
unset ADMIN_ALLOWED_GOOGLE_DOMAINS
unset ADMIN_ALLOWED_MICROSOFT_TENANTS
unset DOMAIN
unset JWT_PRIVATE_KEY_PEM
unset JWT_KID
unset SESSION_SECRET
echo "  Done — agent-service will load these from SSM Parameter Store"

# ── Start Agent Service ──────────────────────────────────────────
echo "[3/3] Starting agent-service..."
cd /workspace/src/workloads/agent-service
source .venv/bin/activate
export PORT=8000
export MCP_SERVER_URL="http://localhost:8001/mcp"
export DATABASE_URL="postgresql://agentops:localdev@localhost:5432/kestral"
python3 src/main.py
