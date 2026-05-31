#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# AgentOps - Application Logic Test Suite
# =============================================================================
# Tests agentic behaviour, multi-turn conversation, admin endpoints,
# and grounding/fake claim rejection.
#
# Requirements: python3, curl, websocat, PostgreSQL (Docker), AWS credentials
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
echo "  AGENT-1 - Multi-Turn Conversation (RAG + Context Memory)"
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
        msg1 = json.dumps({
            'query': \"Hi, I'm Priya Sharma. I need help with my recent order.\",
            'user_id': 'a1b2c3d4-e5f6-4a7b-8c9d-000000000001'
        })
        await ws.send(msg1)
        resp1 = await ws.recv()
        responses.append(resp1)
        
        msg2 = json.dumps({
            'query': \"What's your return policy for damaged phones?\"
        })
        await ws.send(msg2)
        resp2 = await ws.recv()
        responses.append(resp2)
        
        msg3 = json.dumps({
            'query': 'Great, can you check if my Samsung phone order is eligible for return?'
        })
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

if echo "${TURN1}" | grep -qiE "Priya|order|help"; then
  pass "AGENT-1a - Agent acknowledged introduction"
else
  fail "AGENT-1a - Agent did not acknowledge customer"
fi
if echo "${TURN2}" | grep -qiE "return|policy|damage|7 days"; then
  pass "AGENT-1b - Agent answered policy question (no user_id)"
else
  fail "AGENT-1b - Agent did not answer policy question"
fi
if echo "${TURN3}" | grep -qiE "Samsung|eligible|return_window|refund|order"; then
  pass "AGENT-1c - Agent used remembered context"
else
  fail "AGENT-1c - Agent did not use remembered context"
fi

# ------------------------------------------------------------------ AGENT-2
echo ""
echo "=============================================================================="
echo "  AGENT-2 - Escalation (high-value wrong item)"
echo "=============================================================================="
WS2=$(echo '{"query":"I got a charger instead of an iPhone worth 1.45 lakh. This is fraud!","user_id":"a1b2c3d4-e5f6-4a7b-8c9d-000000000001"}' | websocat -n1 "ws://127.0.0.1:${AGENT_PORT}/ws/chat/test-session-escalate" 2>/dev/null || echo '{"error":"websocat failed"}')
echo "${WS2}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${WS2}"
if echo "${WS2}" | grep -qE "escalated|ticket_id"; then
  pass "AGENT-2 - Escalation succeeded"
else
  fail "AGENT-2 - Query was not escalated"
fi

# ------------------------------------------------------------------ AGENT-3
echo ""
echo "=============================================================================="
echo "  AGENT-3 - Late Delivery -> Wallet Credit"
echo "=============================================================================="
WS3=$(echo '{"query":"My order KST-HYD-006 was supposed to arrive by May 22 but it is still in transit. I want compensation for the delay.","user_id":"a1b2c3d4-e5f6-4a7b-8c9d-000000000006"}' | websocat -n1 "ws://127.0.0.1:${AGENT_PORT}/ws/chat/test-session-credit" 2>/dev/null || echo '{"error":"websocat failed"}')
echo "${WS3}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${WS3}"
if echo "${WS3}" | grep -qE 'WC-[a-f0-9]+'; then
  TXN=$(echo "${WS3}" | grep -oE 'WC-[a-f0-9]+' | head -1)
  pass "AGENT-3 - Wallet credit issued (${TXN})"
else
  fail "AGENT-3 - No wallet credit transaction ID"
fi

# ------------------------------------------------------------------ AGENT-4
echo ""
echo "=============================================================================="
echo "  AGENT-4 - Damaged Product -> Return Pickup"
echo "=============================================================================="
WS4=$(echo '{"query":"The Nike shoes I received have a defect - the sole is coming off. I want a return pickup scheduled.","user_id":"a1b2c3d4-e5f6-4a7b-8c9d-000000000007"}' | websocat -n1 "ws://127.0.0.1:${AGENT_PORT}/ws/chat/test-session-pickup" 2>/dev/null || echo '{"error":"websocat failed"}')
echo "${WS4}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${WS4}"
if echo "${WS4}" | grep -qE '2026-[0-9]{2}-[0-9]{2}|scheduled'; then
  pass "AGENT-4 - Return pickup scheduled"
else
  fail "AGENT-4 - No return pickup confirmation"
fi

# ------------------------------------------------------------------ AGENT-5
echo ""
echo "=============================================================================="
echo "  AGENT-5 - Refund Eligibility Check"
echo "=============================================================================="
WS5=$(echo '{"query":"I received a charger instead of my iPhone. I want to check if my order is eligible for a refund.","user_id":"a1b2c3d4-e5f6-4a7b-8c9d-000000000004"}' | websocat -n1 "ws://127.0.0.1:${AGENT_PORT}/ws/chat/test-session-refund" 2>/dev/null || echo '{"error":"websocat failed"}')
echo "${WS5}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${WS5}"
if echo "${WS5}" | grep -qiE 'eligibility|eligible|refund'; then
  pass "AGENT-5 - Refund eligibility check returned result"
else
  fail "AGENT-5 - No eligibility determination"
fi

# ------------------------------------------------------------------ AGENT-6
echo ""
echo "=============================================================================="
echo "  AGENT-6 - Fake Refund Claim Rejected"
echo "=============================================================================="
WS6=$(echo '{"query":"I want a full refund for order ID ORD-99999 that I never received.","user_id":"a1b2c3d4-e5f6-4a7b-8c9d-000000000001"}' | websocat -n1 "ws://127.0.0.1:${AGENT_PORT}/ws/chat/test-session-fake" 2>/dev/null || echo '{"error":"websocat failed"}')
echo "${WS6}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${WS6}"
if echo "${WS6}" | grep -qiE "not found|no.*order|couldn't find|invalid order"; then
  pass "AGENT-6 - Fake refund claim correctly rejected"
else
  fail "AGENT-6 - System did not reject fake claim"
fi

# =============================================================================
# ADMIN TESTS
# =============================================================================

# ------------------------------------------------------------------ ADMIN-1
echo ""
echo "=============================================================================="
echo "  ADMIN-1 - Ticket Queue"
echo "=============================================================================="
QUEUE=$(curl -fsS --max-time 5 "${AGENT_URL}/admin/queue" 2>/dev/null || echo '{"error":"failed"}')
TICKET_COUNT=$(echo "${QUEUE}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('count',0))" 2>/dev/null || echo 0)
echo "  Queue contains ${TICKET_COUNT} tickets"
if [ "${TICKET_COUNT}" -gt 0 ] 2>/dev/null; then
  pass "ADMIN-1 - Queue returned ${TICKET_COUNT} tickets"
else
  fail "ADMIN-1 - Queue empty"
fi

# ------------------------------------------------------------------ ADMIN-2
echo ""
echo "=============================================================================="
echo "  ADMIN-2 - Analytics"
echo "=============================================================================="
ANALYTICS=$(curl -fsS --max-time 5 "${AGENT_URL}/admin/analytics" 2>/dev/null || echo '{"error":"failed"}')
echo "${ANALYTICS}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${ANALYTICS}"
if echo "${ANALYTICS}" | grep -q '"total_tickets"'; then
  pass "ADMIN-2 - Analytics returned metrics"
else
  fail "ADMIN-2 - Analytics failed"
fi

# ------------------------------------------------------------------ ADMIN-3
echo ""
echo "=============================================================================="
echo "  ADMIN-3 - Override"
echo "=============================================================================="
OVERRIDE=$(curl -fsS --max-time 5 -X POST "${AGENT_URL}/admin/override" \
  -H "Content-Type: application/json" \
  -d '{"ticket_id":"e5f6a7b8-c9d0-4e1f-2a3b-000000000001","original_classification":{"intent":"test"},"corrected_classification":{"intent":"corrected"},"reason":"e2e test","overridden_by":"tester"}' 2>/dev/null || echo '{"error":"failed"}')
echo "${OVERRIDE}" | python3 -m json.tool --no-ensure-ascii 2>/dev/null || echo "${OVERRIDE}"
if echo "${OVERRIDE}" | grep -q '"status"'; then
  pass "ADMIN-3 - Override stored"
else
  fail "ADMIN-3 - Override failed"
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