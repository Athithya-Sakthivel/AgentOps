#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# AgentOps — Agentic End‑to‑End Test Suite
# =============================================================================
# Demonstrates:
#   • Real‑time WebSocket chat with LangGraph agent
#   • Policy inquiry (auto‑resolved by Bedrock)
#   • Late delivery → automatic wallet credit
#   • Damaged product → return pickup scheduling
#   • Refund eligibility check
#   • High‑urgency escalation → ticket creation
#   • Admin REST endpoints (queue, analytics, overrides)
#   • Cross‑service structured logging with correlation IDs
#
# Requirements: python3, curl, websocat, PostgreSQL (Docker), AWS credentials
# =============================================================================

# ── Config ──────────────────────────────────────────────────────────────────
MCP_PORT="${MCP_PORT:-8001}"
AGENT_PORT="${AGENT_PORT:-8000}"
MCP_URL="http://127.0.0.1:${MCP_PORT}"
AGENT_URL="http://127.0.0.1:${AGENT_PORT}"

export DATABASE_URL="${DATABASE_URL:-postgresql://agentops:localdev@localhost:5432/kestral}"
export LLM_API_KEY="${LLM_API_KEY:-}"            # Not required for Bedrock

command -v python3  >/dev/null 2>&1 || { echo "[FATAL] python3 missing"  >&2; exit 1; }
command -v curl     >/dev/null 2>&1 || { echo "[FATAL] curl missing"     >&2; exit 1; }
command -v websocat >/dev/null 2>&1 || { echo "[FATAL] websocat missing" >&2; exit 1; }

MCP_PID=""
AGENT_PID=""
PASSED=0
FAILED=0

# ── Helpers ─────────────────────────────────────────────────────────────────
cleanup() {
  set +e
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  🧹  Cleaning up …"
  kill -INT "${AGENT_PID}" 2>/dev/null || true
  kill -INT "${MCP_PID}" 2>/dev/null || true
  sleep 2
  echo "  ✅  Done."
}
trap cleanup EXIT

pass() { PASSED=$((PASSED+1)); echo "  ✅  PASS │ $*"; }
fail() { FAILED=$((FAILED+1)); echo "  ❌  FAIL │ $*"; }

# ── 1. Start mcp-server ─────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ⓵  Starting MCP Server"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

lsof -ti tcp:${MCP_PORT} | xargs kill -9 2>/dev/null || true

cd src/workloads/mcp-server
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q -r requirements.txt 2>/dev/null || true

export PORT="${MCP_PORT}"
export LOG_LEVEL="INFO"

python3 src/main.py > /tmp/mcp-server-e2e.log 2>&1 &
MCP_PID=$!

for ((i=0; i<20; i++)); do
  if curl -fsS --max-time 2 "${MCP_URL}/readyz" 2>/dev/null | grep -q "ready"; then
    echo "  ✅  mcp-server ready (PID ${MCP_PID})"
    break
  fi
  sleep 1
done
cd - >/dev/null

# ── 2. Start agent-service ──────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ⓶  Starting Agent Service"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

cd src/workloads/agent-service
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q -r requirements.txt 2>/dev/null || true

export MCP_SERVER_URL="${MCP_URL}/mcp"
export PORT="${AGENT_PORT}"
export LOG_LEVEL="INFO"

python3 src/main.py > /tmp/agent-service-e2e.log 2>&1 &
AGENT_PID=$!

for ((i=0; i<30; i++)); do
  if curl -fsS --max-time 2 "${AGENT_URL}/readyz" 2>/dev/null | grep -q "ready"; then
    echo "  ✅  agent-service ready (PID ${AGENT_PID})"
    break
  fi
  sleep 1
done
cd - >/dev/null

# ── 3. Test Suite ───────────────────────────────────────────────────────────

# ------------------------------------------------------------------ Test 1
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  TEST 1  •  WebSocket — Policy Inquiry"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
QUERY1='{"query":"What is the return policy for damaged phones?","user_id":"a1b2c3d4-e5f6-4a7b-8c9d-000000000001"}'
echo "  📤  SENDING: ${QUERY1}"

WS1=$(echo "${QUERY1}" | websocat -n1 "ws://127.0.0.1:${AGENT_PORT}/ws/chat/test-session-1" 2>/dev/null || echo '{"error":"websocat failed"}')
echo "  📥  RESPONSE:"
echo "${WS1}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${WS1}"

if echo "${WS1}" | grep -qE '"response"'; then
  pass "Agent returned a grounded response (auto‑resolved)"
else
  fail "No 'response' field in output"
fi

# ------------------------------------------------------------------ Test 2
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  TEST 2  •  WebSocket — Escalation"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
QUERY2='{"query":"I got a charger instead of an iPhone worth 1.45 lakh. This is fraud!","user_id":"a1b2c3d4-e5f6-4a7b-8c9d-000000000001"}'
echo "  📤  SENDING: ${QUERY2}"

WS2=$(echo "${QUERY2}" | websocat -n1 "ws://127.0.0.1:${AGENT_PORT}/ws/chat/test-session-2" 2>/dev/null || echo '{"error":"websocat failed"}')
echo "  📥  RESPONSE:"
echo "${WS2}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${WS2}"

if echo "${WS2}" | grep -qE "escalated|ticket_id"; then
  TICKET=$(echo "${WS2}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('ticket_id','none'))" 2>/dev/null || echo "parse-error")
  pass "Escalation succeeded — ticket_id=${TICKET}"
else
  fail "Query was not escalated"
fi

# ------------------------------------------------------------------ Test 3
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  TEST 3  •  Agentic Action — Late Delivery → Wallet Credit"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
QUERY3='{"query":"My order KST-HYD-006 was supposed to arrive by May 22, but it is still in transit. I want compensation for the delay.","user_id":"a1b2c3d4-e5f6-4a7b-8c9d-000000000006"}'

WS3=$(echo "${QUERY3}" | websocat -n1 "ws://127.0.0.1:${AGENT_PORT}/ws/chat/test-session-3" 2>/dev/null || echo '{"error":"websocat failed"}')
echo "  📥  RESPONSE:"
echo "${WS3}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${WS3}"

if echo "${WS3}" | grep -qi "store credit\|transaction"; then
  pass "Late delivery → wallet credit issued"
else
  fail "Expected wallet credit message not found"
fi

# ------------------------------------------------------------------ Test 4
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  TEST 4  •  Agentic Action — Damaged Product → Return Pickup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
QUERY4='{"query":"The Nike shoes I received have a defect – the sole is coming off. I want a return pickup scheduled.","user_id":"a1b2c3d4-e5f6-4a7b-8c9d-000000000007"}'

WS4=$(echo "${QUERY4}" | websocat -n1 "ws://127.0.0.1:${AGENT_PORT}/ws/chat/test-session-4" 2>/dev/null || echo '{"error":"websocat failed"}')
echo "  📥  RESPONSE:"
echo "${WS4}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${WS4}"

if echo "${WS4}" | grep -qiE "scheduled|pickup|return"; then
  pass "Damaged product → return pickup attempted"
else
  fail "No return pickup message found"
fi

# ------------------------------------------------------------------ Test 5
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  TEST 5  •  Agentic Action — Refund Eligibility Check"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
QUERY5='{"query":"I received a charger instead of my iPhone. I want to check if my order is eligible for a refund.","user_id":"a1b2c3d4-e5f6-4a7b-8c9d-000000000004"}'

WS5=$(echo "${QUERY5}" | websocat -n1 "ws://127.0.0.1:${AGENT_PORT}/ws/chat/test-session-5" 2>/dev/null || echo '{"error":"websocat failed"}')
echo "  📥  RESPONSE:"
echo "${WS5}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${WS5}"

if echo "${WS5}" | grep -qiE "eligible|refund"; then
  pass "Refund eligibility check succeeded"
else
  fail "No eligibility information found"
fi

# ------------------------------------------------------------------ Test 6
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  TEST 6  •  Admin — Ticket Queue"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  📤  GET ${AGENT_URL}/admin/queue"

QUEUE=$(curl -fsS --max-time 5 "${AGENT_URL}/admin/queue" 2>/dev/null || echo '{"error":"failed"}')
echo "  📥  RESPONSE:"
echo "${QUEUE}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${QUEUE}"

TICKET_COUNT=$(echo "${QUEUE}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('count',0))" 2>/dev/null || echo 0)
if [ "${TICKET_COUNT}" -gt 0 ] 2>/dev/null; then
  pass "Queue contains ${TICKET_COUNT} tickets"
else
  fail "Queue empty — expected tickets after escalation test"
fi

# ------------------------------------------------------------------ Test 7
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  TEST 7  •  Admin — Analytics"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  📤  GET ${AGENT_URL}/admin/analytics"

ANALYTICS=$(curl -fsS --max-time 5 "${AGENT_URL}/admin/analytics" 2>/dev/null || echo '{"error":"failed"}')
echo "  📥  RESPONSE:"
echo "${ANALYTICS}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${ANALYTICS}"

if echo "${ANALYTICS}" | grep -q '"total_tickets"'; then
  pass "Analytics returned metrics"
else
  fail "Analytics endpoint failed"
fi

# ------------------------------------------------------------------ Test 8
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  TEST 8  •  Admin — Override"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
OVERRIDE_PAYLOAD='{"ticket_id":"e5f6a7b8-c9d0-4e1f-2a3b-000000000001","original_classification":{"intent":"test"},"corrected_classification":{"intent":"corrected"},"reason":"e2e test","overridden_by":"tester"}'
echo "  📤  POST ${AGENT_URL}/admin/override"
echo "       ${OVERRIDE_PAYLOAD}"

OVERRIDE=$(curl -fsS --max-time 5 -X POST "${AGENT_URL}/admin/override" \
  -H "Content-Type: application/json" \
  -d "${OVERRIDE_PAYLOAD}" 2>/dev/null || echo '{"error":"failed"}')

echo "  📥  RESPONSE:"
echo "${OVERRIDE}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${OVERRIDE}"

if echo "${OVERRIDE}" | grep -q '"status"'; then
  pass "Override stored"
else
  fail "Override failed"
fi

# ------------------------------------------------------------------ Test 9
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  TEST 9  •  Observability — Agent Logs with Correlation ID"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  📤  Checking /tmp/agent-service-e2e.log"

AGENT_RUN_COUNT=$(grep -cE '"run_id": "[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"' /tmp/agent-service-e2e.log 2>/dev/null || echo 0)
echo "  📥  Log lines with a valid run_id UUID: ${AGENT_RUN_COUNT}"

if [ "${AGENT_RUN_COUNT}" -gt 0 ] 2>/dev/null; then
  pass "${AGENT_RUN_COUNT} agent log lines include a correlation ID"
else
  fail "No correlation IDs found in agent logs"
fi

# ------------------------------------------------------------------ Test 10
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  TEST 10  •  Observability — MCP Server Logs with Propagated run_id"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  📤  Checking /tmp/mcp-server-e2e.log"

MCP_RUN_COUNT=$(grep -cE '"run_id": "[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"' /tmp/mcp-server-e2e.log 2>/dev/null || echo 0)
echo "  📥  Log lines with a valid run_id UUID: ${MCP_RUN_COUNT}"

if [ "${MCP_RUN_COUNT}" -gt 0 ] 2>/dev/null; then
  pass "${MCP_RUN_COUNT} MCP server log lines include a correlation ID (propagated from agent)"
else
  fail "No correlation IDs in MCP logs — run_id not propagated?"
fi

# ------------------------------------------------------------------ Test 11
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  TEST 11  •  Observability — Structured JSON Format"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  📤  Checking /tmp/agent-service-e2e.log for structured JSON lines"

STRUCTURED_COUNT=$(grep -cE '"timestamp".*"level".*"message"' /tmp/agent-service-e2e.log 2>/dev/null || echo 0)
echo "  📥  Lines containing timestamp, level, and message: ${STRUCTURED_COUNT}"

if [ "${STRUCTURED_COUNT}" -gt 0 ] 2>/dev/null; then
  pass "${STRUCTURED_COUNT} structured JSON log lines found"
else
  fail "No structured JSON log lines — expected timestamp/level/message fields"
fi

# ── Final Summary ───────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🏁  TEST SUITE COMPLETE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅  PASSED : ${PASSED}"
echo "  ❌  FAILED : ${FAILED}"
echo ""

if [ "${FAILED}" -eq 0 ]; then
  echo "  🎉  AgentOps is fully operational and agentic."
  exit 0
else
  echo "  🔧  ${FAILED} test(s) failed — review the output above for details."
  exit 1
fi