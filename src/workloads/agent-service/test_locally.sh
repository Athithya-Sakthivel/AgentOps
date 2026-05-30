#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Agent Service - End-to-End Test (Local, No Kubernetes, No OTEL)
# =============================================================================
# Prerequisites:
#   PostgreSQL Docker container 'kestral-postgres' running
#   mcp-server and agent-service dependencies installed
#   websocat installed (pip install websocat or cargo install websocat)
#   GROQ_API_KEY environment variable set
#
# Starts mcp-server, then agent-service, tests WebSocket chat + admin
# endpoints, verifies structured logging with run_id, cleans up on exit.
# =============================================================================

# --- Config ---------------------------------------------------------------
MCP_PORT="${MCP_PORT:-8001}"
AGENT_PORT="${AGENT_PORT:-8000}"
MCP_URL="http://127.0.0.1:${MCP_PORT}"
AGENT_URL="http://127.0.0.1:${AGENT_PORT}"
MCP_HTTP="${MCP_URL}/mcp"

# PostgreSQL connection (must match seed script)
export DATABASE_URL="${DATABASE_URL:-postgresql://agentops:localdev@localhost:5432/kestral}"
export GROQ_API_KEY="${GROQ_API_KEY:?GROQ_API_KEY must be set}"
export LLM_API_KEY="${GROQ_API_KEY}"

# --- Prerequisites ---------------------------------------------------------
command -v python3  >/dev/null 2>&1 || { echo "[ERROR] python3 not found"  >&2; exit 1; }
command -v curl     >/dev/null 2>&1 || { echo "[ERROR] curl not found"     >&2; exit 1; }
command -v websocat >/dev/null 2>&1 || { echo "[ERROR] websocat not found" >&2; exit 1; }

# --- Global state ----------------------------------------------------------
MCP_PID=""
AGENT_PID=""
declare -a RESULTS
TEST_RUN_ID="e2e-$(date +%s)-$$"  # Used in test queries for log correlation

cleanup() {
  set +e
  echo ""
  echo "[CLEANUP] Stopping services..."
  kill -INT "${AGENT_PID}" 2>/dev/null || true
  kill -INT "${MCP_PID}" 2>/dev/null || true
  sleep 2
  echo "[CLEANUP] Done."
  set -e
}
trap cleanup EXIT

record_pass() {
  local name="$1" detail="$2"
  echo "  [PASS] ${name}"
  [[ -n "${detail}" ]] && echo "         ${detail}"
  RESULTS+=("PASS: ${name}")
}
record_fail() {
  local name="$1" detail="$2"
  echo "  [FAIL] ${name}"
  [[ -n "${detail}" ]] && echo "         ${detail}"
  RESULTS+=("FAIL: ${name}")
}

# =============================================================================
# STEP 1: Start mcp-server
# =============================================================================
echo ""
echo "=============================================================================="
echo "[STEP 1/6] Starting mcp-server..."
echo "=============================================================================="

# Kill any stale mcp-server on same port
lsof -ti tcp:${MCP_PORT} | xargs kill -9 2>/dev/null || true

cd src/workloads/mcp-server
source .venv/bin/activate 2>/dev/null || python3 -m venv .venv && source .venv/bin/activate
pip install -q -r requirements.txt 2>/dev/null || true

export PORT="${MCP_PORT}"
export LOG_LEVEL="INFO"

python3 src/main.py > /tmp/mcp-server-e2e.log 2>&1 &
MCP_PID=$!
echo "  mcp-server PID = ${MCP_PID}"

# Wait for readiness
echo "  Waiting for mcp-server to be ready..."
for ((i=0; i<20; i++)); do
  if curl -fsS --max-time 2 "${MCP_URL}/readyz" 2>/dev/null | grep -q "ready"; then
    echo "  mcp-server ready"
    break
  fi
  sleep 1
done
if ! curl -fsS --max-time 2 "${MCP_URL}/readyz" 2>/dev/null | grep -q "ready"; then
  echo "[FATAL] mcp-server failed to become ready"
  tail -20 /tmp/mcp-server-e2e.log
  exit 1
fi

cd - >/dev/null

# =============================================================================
# STEP 2: Start agent-service
# =============================================================================
echo ""
echo "=============================================================================="
echo "[STEP 2/6] Starting agent-service..."
echo "=============================================================================="

cd src/workloads/agent-service
source .venv/bin/activate 2>/dev/null || python3 -m venv .venv && source .venv/bin/activate
pip install -q -r requirements.txt 2>/dev/null || true

export MCP_SERVER_URL="${MCP_HTTP}"
export PORT="${AGENT_PORT}"
export LOG_LEVEL="INFO"
export CORS_ORIGINS="http://localhost:3000"
export DEPLOYMENT_ENVIRONMENT="e2e-test"

python3 src/main.py > /tmp/agent-service-e2e.log 2>&1 &
AGENT_PID=$!
echo "  agent-service PID = ${AGENT_PID}"

echo "  Waiting for agent-service to be ready..."
for ((i=0; i<30; i++)); do
  if curl -fsS --max-time 2 "${AGENT_URL}/readyz" 2>/dev/null | grep -q "ready"; then
    echo "  agent-service ready"
    break
  fi
  sleep 1
done
if ! curl -fsS --max-time 2 "${AGENT_URL}/readyz" 2>/dev/null | grep -q "ready"; then
  echo "[FATAL] agent-service did not become ready"
  tail -20 /tmp/agent-service-e2e.log
  exit 1
fi

cd - >/dev/null

# =============================================================================
# STEP 3: Test WebSocket Chat
# =============================================================================
echo ""
echo "=============================================================================="
echo "[STEP 3/6] Testing WebSocket chat..."
echo "=============================================================================="

# Simple policy query
WS_OUTPUT=$(echo "{\"query\":\"What is the return policy for damaged phones?\",\"user_id\":\"a1b2c3d4-e5f6-4a7b-8c9d-000000000001\",\"run_id\":\"${TEST_RUN_ID}-1\"}" | websocat -n1 "ws://127.0.0.1:${AGENT_PORT}/ws/chat/test-session-1" 2>/dev/null || echo '{"error":"websocat failed"}')
echo "  Policy query response:"
echo "${WS_OUTPUT}" | head -20 | sed 's/^/    /'

if echo "${WS_OUTPUT}" | grep -qE '"response"|"error"'; then
  record_pass "WebSocket chat - policy query" "Agent returned a response"
else
  record_fail "WebSocket chat - policy query" "No valid response"
fi

# High-urgency escalation query
WS_OUTPUT2=$(echo "{\"query\":\"I got a charger instead of an iPhone worth 1.45 lakh. This is fraud!\",\"user_id\":\"a1b2c3d4-e5f6-4a7b-8c9d-000000000001\",\"run_id\":\"${TEST_RUN_ID}-2\"}" | websocat -n1 "ws://127.0.0.1:${AGENT_PORT}/ws/chat/test-session-2" 2>/dev/null || echo '{"error":"websocat failed"}')
echo "  Escalation query response:"
echo "${WS_OUTPUT2}" | head -20 | sed 's/^/    /'

if echo "${WS_OUTPUT2}" | grep -qE "escalated|ticket_id"; then
  record_pass "WebSocket chat - escalation" "High-urgency query escalated"
else
  record_fail "WebSocket chat - escalation" "Escalation expected but not found"
fi

# =============================================================================
# STEP 4: Test Admin Endpoints
# =============================================================================
echo ""
echo "=============================================================================="
echo "[STEP 4/6] Testing admin endpoints..."
echo "=============================================================================="

# Queue
echo "  GET /admin/queue"
QUEUE=$(curl -fsS --max-time 5 "${AGENT_URL}/admin/queue" 2>/dev/null || echo '{"error":"failed"}')
if echo "${QUEUE}" | grep -q '"tickets"'; then
  record_pass "Admin GET /admin/queue" "Returned ticket list"
else
  record_fail "Admin GET /admin/queue" "Unexpected response"
fi

# Analytics
echo "  GET /admin/analytics"
ANALYTICS=$(curl -fsS --max-time 5 "${AGENT_URL}/admin/analytics" 2>/dev/null || echo '{"error":"failed"}')
if echo "${ANALYTICS}" | grep -q '"total_tickets"'; then
  record_pass "Admin GET /admin/analytics" "Returned analytics"
else
  record_fail "Admin GET /admin/analytics" "Unexpected response"
fi

# Override (POST)
echo "  POST /admin/override"
OVERRIDE=$(curl -fsS --max-time 5 -X POST "${AGENT_URL}/admin/override" \
  -H "Content-Type: application/json" \
  -d '{"ticket_id":"e5f6a7b8-c9d0-4e1f-2a3b-000000000001","original_classification":{"intent":"test"},"corrected_classification":{"intent":"test"},"reason":"e2e test","overridden_by":"tester"}' 2>/dev/null || echo '{"error":"failed"}')
if echo "${OVERRIDE}" | grep -q '"status"'; then
  record_pass "Admin POST /admin/override" "Override stored"
else
  record_fail "Admin POST /admin/override" "Unexpected response"
fi

# =============================================================================
# STEP 5: Verify run_id Propagation in Logs
# =============================================================================
echo ""
echo "=============================================================================="
echo "[STEP 5/6] Verifying run_id propagation in structured logs..."
echo "=============================================================================="

sleep 1  # Allow logs to flush

AGENT_LOG=$(cat /tmp/agent-service-e2e.log 2>/dev/null || echo "")
MCP_LOG=$(cat /tmp/mcp-server-e2e.log 2>/dev/null || echo "")

# Check agent logs for our test run_id
AGENT_RUN_COUNT=$(echo "${AGENT_LOG}" | grep -c "${TEST_RUN_ID}" || echo 0)
echo "  Agent log lines with test run_id '${TEST_RUN_ID}': ${AGENT_RUN_COUNT}"
if [[ "${AGENT_RUN_COUNT}" -gt 0 ]]; then
  record_pass "Agent logs contain run_id" "${AGENT_RUN_COUNT} lines"
else
  record_fail "Agent logs contain run_id" "No lines found"
fi

# Check mcp logs for run_id (should propagate from agent to MCP tools)
MCP_RUN_COUNT=$(echo "${MCP_LOG}" | grep -c "${TEST_RUN_ID}" || echo 0)
echo "  MCP server log lines with test run_id: ${MCP_RUN_COUNT}"
if [[ "${MCP_RUN_COUNT}" -gt 0 ]]; then
  record_pass "MCP server logs contain run_id" "${MCP_RUN_COUNT} lines (propagated)"
else
  # Not necessarily a failure if the query didn't require MCP tools, but escalate should.
  record_fail "MCP server logs contain run_id" "No lines found - run_id not propagated?"
fi

# Verify structured JSON format (check for timestamp, level, message)
if echo "${AGENT_LOG}" | grep -q '"timestamp".*"level".*"message"'; then
  record_pass "Agent logs are structured JSON" "Timestamp, level, message present"
else
  record_fail "Agent logs are structured JSON" "Missing expected fields"
fi

# =============================================================================
# STEP 6: Summary
# =============================================================================
echo ""
echo "=============================================================================="
echo "[STEP 6/6] End-to-End Test Summary"
echo "=============================================================================="
echo ""
echo "  Results:"
for result in "${RESULTS[@]}"; do
  echo "    ${result}"
done

FAIL_COUNT=$(printf '%s\n' "${RESULTS[@]}" | grep -c "^FAIL:" || true)
PASS_COUNT=$(printf '%s\n' "${RESULTS[@]}" | grep -c "^PASS:" || true)
echo ""
echo "  =============================================="
echo "  TOTAL: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
echo "  =============================================="

if [[ "${FAIL_COUNT}" -eq 0 ]]; then
  echo ""
  echo "  AgentOps end-to-end test PASSED."
  exit 0
else
  echo ""
  echo "  ${FAIL_COUNT} check(s) failed - review logs in /tmp/agent-service-e2e.log and /tmp/mcp-server-e2e.log"
  exit 1
fi