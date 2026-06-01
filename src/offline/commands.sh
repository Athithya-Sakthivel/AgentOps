#!/bin/bash
set -euo pipefail

# =============================================================================
# AgentOps — Local Startup Commands
# =============================================================================
# IMPORTANT: Secrets MUST come from SSM, not environment variables.
# Unset them before starting agent-service to catch SSM config bugs early.
# =============================================================================

# ── Start MCP Server (no AWS secrets needed) ──────────────────────
echo "[1/3] Starting mcp-server..."
cd /workspace/src/workloads/mcp-server
source .venv/bin/activate
export PORT=8001
export DATABASE_URL="postgresql://agentops:localdev@localhost:5432/kestral"
python3 src/main.py > /tmp/mcp-server.log 2>&1 &
MCP_PID=$!
sleep 3
curl -s http://localhost:8001/readyz && echo "  mcp-server ready" || echo "  mcp-server FAILED"

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
# Only env vars that are NOT in SSM remain: PORT, MCP_SERVER_URL, DATABASE_URL, LOG_LEVEL
python3 src/main.py > /tmp/agent-service.log 2>&1 &
AGENT_PID=$!
sleep 10
curl -s http://localhost:8000/healthz && echo "  agent-service ready" || echo "  agent-service FAILED"

echo ""
echo "Both services running:"
echo "  mcp-server    PID $MCP_PID  → http://localhost:8001"
echo "  agent-service PID $AGENT_PID → http://localhost:8000"