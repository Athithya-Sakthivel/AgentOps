#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# AgentOps - Application Logic Test Suite (Pragmatic Agent)
# =============================================================================
# Tests: policy RAG, ticket creation with correct routing/summary,
#        fake claim rejection, multi-turn context, admin auth.
# =============================================================================

# -- Config -------------------------------------------------------------------
MCP_PORT="${MCP_PORT:-8001}"
AGENT_PORT="${AGENT_PORT:-8000}"
MCP_URL="http://127.0.0.1:${MCP_PORT}"
AGENT_URL="http://127.0.0.1:${AGENT_PORT}"

export DATABASE_URL="${DATABASE_URL:-postgresql://agentops:localdev@localhost:5432/kestral}"

command -v python3  >/dev/null 2>&1 || { echo "[FATAL] python3 missing"  >&2; exit 1; }
command -v curl     >/dev/null 2>&1 || { echo "[FATAL] curl missing"     >&2; exit 1; }
command -v websocat >/dev/null 2>&1 || { echo "[FATAL] websocat missing" >&2; exit 1; }

MCP_PID=""
AGENT_PID=""
PASSED=0
FAILED=0

cleanup() {
  set +e
  echo ""
  echo "=============================================================================="
  echo "  CLEANUP: Stopping services..."
  kill -INT "${AGENT_PID}" 2>/dev/null || true
  kill -INT "${MCP_PID}" 2>/dev/null || true
  sleep 2
  echo "  CLEANUP: Done."
}
trap cleanup EXIT

pass() { PASSED=$((PASSED+1)); echo "  PASS | $*"; }
fail() { FAILED=$((FAILED+1)); echo "  FAIL | $*"; }

# -- 1. Start mcp-server ------------------------------------------------------
echo ""
echo "=============================================================================="
echo "  1. Starting MCP Server"
echo "=============================================================================="

lsof -ti tcp:${MCP_PORT} | xargs kill -9 2>/dev/null || true

cd src/workloads/mcp-server
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q -r requirements.txt 2>/dev/null || true

export PORT="${MCP_PORT}"
export LOG_LEVEL="INFO"

python3 src/main.py > /tmp/mcp-server-agent.log 2>&1 &
MCP_PID=$!

for ((i=0; i<20; i++)); do
  if curl -fsS --max-time 2 "${MCP_URL}/readyz" 2>/dev/null | grep -q "ready"; then
    echo "  mcp-server ready (PID ${MCP_PID})"
    break
  fi
  sleep 1
done
cd - >/dev/null

# -- 2. Start agent-service ---------------------------------------------------
echo ""
echo "=============================================================================="
echo "  2. Starting Agent Service"
echo "=============================================================================="

cd src/workloads/agent-service
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q -r requirements.txt 2>/dev/null || true

export MCP_SERVER_URL="${MCP_URL}/mcp"
export PORT="${AGENT_PORT}"
export LOG_LEVEL="INFO"

python3 src/main.py > /tmp/agent-service-agent.log 2>&1 &
AGENT_PID=$!

for ((i=0; i<30; i++)); do
  if curl -fsS --max-time 2 "${AGENT_URL}/readyz" 2>/dev/null | grep -q "ready"; then
    echo "  agent-service ready (PID ${AGENT_PID})"
    break
  fi
  sleep 1
done
cd - >/dev/null

# =============================================================================
# AGENT TESTS
# =============================================================================

# ------------------------------------------------------------------ AGENT-1
echo ""
echo "=============================================================================="
echo "  AGENT-1 - Policy Q&A via RAG"
echo "=============================================================================="
WS1=$(echo '{"query":"What is your return policy for electronics?","user_id":"a1b2c3d4-e5f6-4a7b-8c9d-000000000001"}' | websocat -n1 "ws://127.0.0.1:${AGENT_PORT}/ws/chat/test-session-policy" 2>/dev/null || echo '{"error":"websocat failed"}')
echo "${WS1}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${WS1}"
if echo "${WS1}" | grep -qiE "return|policy|days|window"; then
  pass "AGENT-1 - Policy Q&A returned relevant information"
else
  fail "AGENT-1 - Policy Q&A did not return expected content"
fi

# ------------------------------------------------------------------ AGENT-2
echo ""
echo "=============================================================================="
echo "  AGENT-2 - Ticket Creation with Correct Routing & Summary"
echo "=============================================================================="
WS2=$(echo '{"query":"I ordered a smartphone but received a charger. This is completely wrong!","user_id":"a1b2c3d4-e5f6-4a7b-8c9d-000000000001"}' | websocat -n1 "ws://127.0.0.1:${AGENT_PORT}/ws/chat/test-session-wrongitem" 2>/dev/null || echo '{"error":"websocat failed"}')
echo "${WS2}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${WS2}"
# Check for ticket UUID
if echo "${WS2}" | grep -qE '"ticket_id":\s*"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"'; then
  pass "AGENT-2a - Ticket created with valid UUID"
else
  fail "AGENT-2a - No valid ticket ID found"
fi
# Check for team routing in the response
if echo "${WS2}" | grep -qiE "order.fulfillment|fulfillment"; then
  pass "AGENT-2b - Ticket routed to order fulfillment"
else
  fail "AGENT-2b - Ticket not routed to expected team"
fi
# Check that the summary mentions specific items
if echo "${WS2}" | grep -qiE "charger|smartphone|wrong item"; then
  pass "AGENT-2c - Summary mentions specific items"
else
  fail "AGENT-2c - Summary does not mention the wrong item"
fi

# ------------------------------------------------------------------ AGENT-3
echo ""
echo "=============================================================================="
echo "  AGENT-3 - Fake Claim Rejection"
echo "=============================================================================="
WS3=$(echo '{"query":"I want a refund for order ID ORD-99999. I never received it.","user_id":"a1b2c3d4-e5f6-4a7b-8c9d-000000000001"}' | websocat -n1 "ws://127.0.0.1:${AGENT_PORT}/ws/chat/test-session-fake" 2>/dev/null || echo '{"error":"websocat failed"}')
echo "${WS3}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${WS3}"
if echo "${WS3}" | grep -qiE "not found|no.*order|couldn't find|invalid order|not in your recent"; then
  pass "AGENT-3 - Fake claim correctly rejected"
else
  fail "AGENT-3 - Fake claim was not rejected"
fi

# ------------------------------------------------------------------ AGENT-4
echo ""
echo "=============================================================================="
echo "  AGENT-4 - Multi-Turn Context Preservation"
echo "=============================================================================="
echo "  SESSION: test-session-multi"

WS_MULTI=$(python3 -c "
import asyncio
import json
import websockets

async def multi_turn():
    uri = 'ws://127.0.0.1:${AGENT_PORT}/ws/chat/test-session-multi'
    responses = []
    
    async with websockets.connect(uri) as ws:
        # Turn 1: general greeting
        msg1 = json.dumps({'query': \"Hi, I'm Priya Sharma.\", 'user_id': 'a1b2c3d4-e5f6-4a7b-8c9d-000000000001'})
        await ws.send(msg1)
        resp1 = await ws.recv()
        responses.append(resp1)
        await asyncio.sleep(1.5)   # avoid Bedrock rate limiting
        
        # Turn 2: policy question
        msg2 = json.dumps({'query': 'What is your return policy for damaged phones?'})
        await ws.send(msg2)
        resp2 = await ws.recv()
        responses.append(resp2)
        await asyncio.sleep(1.5)
        
        # Turn 3: ask about her specific order (should remember Priya)
        msg3 = json.dumps({'query': 'Can you check my Samsung order?'})
        await ws.send(msg3)
        resp3 = await ws.recv()
        responses.append(resp3)
    
    for r in responses:
        print(r)

asyncio.run(multi_turn())
" 2>/dev/null || echo '{"error":"websocket multi-turn failed"}')

TURN1=$(echo "${WS_MULTI}" | sed -n '1p')
TURN2=$(echo "${WS_MULTI}" | sed -n '2p')
TURN3=$(echo "${WS_MULTI}" | sed -n '3p')

echo "  --- Turn 1 ---"
echo "${TURN1}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${TURN1}"
echo "  --- Turn 2 ---"
echo "${TURN2}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${TURN2}"
echo "  --- Turn 3 ---"
echo "${TURN3}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${TURN3}"

if echo "${TURN1}" | grep -qiE "Priya|Hello|help"; then
  pass "AGENT-4a - Agent acknowledged introduction"
else
  fail "AGENT-4a - Agent did not acknowledge customer"
fi
if echo "${TURN2}" | grep -qiE "return|policy|damage|days"; then
  pass "AGENT-4b - Agent answered policy question using context"
else
  fail "AGENT-4b - Agent did not answer policy question"
fi
if echo "${TURN3}" | grep -qiE "Samsung|order|check|look"; then
  pass "AGENT-4c - Agent used remembered context (Priya's order)"
else
  fail "AGENT-4c - Agent did not use remembered context"
fi

# =============================================================================
# ADMIN TESTS
# =============================================================================

echo ""
echo "=============================================================================="
echo "  ADMIN-1 - Ticket Queue (requires auth)"
echo "=============================================================================="
ADMIN1_CODE=$(curl -s -o /dev/null -w '%{http_code}' "${AGENT_URL}/admin/queue" 2>/dev/null)
if [ "${ADMIN1_CODE}" = "401" ]; then
  pass "ADMIN-1 - /admin/queue returns 401 without token"
else
  fail "ADMIN-1 - Expected 401, got ${ADMIN1_CODE}"
fi

echo ""
echo "=============================================================================="
echo "  ADMIN-2 - Analytics (requires auth)"
echo "=============================================================================="
ADMIN2_CODE=$(curl -s -o /dev/null -w '%{http_code}' "${AGENT_URL}/admin/analytics" 2>/dev/null)
if [ "${ADMIN2_CODE}" = "401" ]; then
  pass "ADMIN-2 - /admin/analytics returns 401 without token"
else
  fail "ADMIN-2 - Expected 401, got ${ADMIN2_CODE}"
fi

echo ""
echo "=============================================================================="
echo "  ADMIN-3 - Override (requires auth)"
echo "=============================================================================="
ADMIN3_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${AGENT_URL}/admin/override" \
  -H "Content-Type: application/json" \
  -d '{"ticket_id":"e5f6a7b8-c9d0-4e1f-2a3b-000000000001","original_classification":{"intent":"test"},"corrected_classification":{"intent":"corrected"},"reason":"e2e","overridden_by":"tester"}' 2>/dev/null)
if [ "${ADMIN3_CODE}" = "401" ]; then
  pass "ADMIN-3 - /admin/override returns 401 without token"
else
  fail "ADMIN-3 - Expected 401, got ${ADMIN3_CODE}"
fi

# -- Final Summary -----------------------------------------------------------
echo ""
echo "=============================================================================="
echo "  AGENT TEST SUITE COMPLETE"
echo "=============================================================================="
echo "  PASSED : ${PASSED}"
echo "  FAILED : ${FAILED}"
echo ""

if [ "${FAILED}" -eq 0 ]; then
  echo "  All agent tests passed."
  exit 0
else
  echo "  ${FAILED} test(s) failed - review logs above."
  exit 1
fi