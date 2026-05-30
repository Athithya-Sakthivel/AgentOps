#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# MCP Server — Local Test (No Kubernetes, No OTEL, No Qdrant)
# =============================================================================
# Tests the 9 MCP tools against a local PostgreSQL instance.
# Uses structured JSON logging with correlation IDs (run_id).
# No OpenTelemetry, no vector database, no external dependencies except Docker.

# --- Config ---------------------------------------------------------------
MCP_PORT="${MCP_PORT:-8001}"
MCP_URL="http://127.0.0.1:${MCP_PORT}"
MCP_HTTP="${MCP_URL}/mcp"          # FastMCP HTTP transport

# PostgreSQL (must be running via Docker, as set up by setup_postgres.py)
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-agentops}"
POSTGRES_DB="${POSTGRES_DB:-kestral}"
# Password is hardcoded 'localdev' in the seed script; we'll use it directly.
DATABASE_URL="postgresql://agentops:localdev@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"

# We'll generate a unique run_id for each test to validate correlation.
RUN_ID="test-$(date +%s)-$$"

# --- Prerequisites ---------------------------------------------------------
command -v python3  >/dev/null 2>&1 || { echo "[ERROR] python3 not found"  >&2; exit 1; }
command -v curl     >/dev/null 2>&1 || { echo "[ERROR] curl not found"     >&2; exit 1; }
command -v fastmcp  >/dev/null 2>&1 || { echo "[ERROR] fastmcp not found. Install with: pip install fastmcp"  >&2; exit 1; }

# --- Global state ----------------------------------------------------------
MCP_PID=""
declare -a RESULTS

cleanup() {
  set +e
  echo ""
  echo "[CLEANUP] Stopping mcp-server (PID ${MCP_PID})..."
  kill -INT "${MCP_PID}" 2>/dev/null || true
  sleep 1
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
# STEP 1: Verify PostgreSQL is running and reachable
# =============================================================================
echo ""
echo "=============================================================================="
echo "[STEP 1/4] Checking PostgreSQL..."
echo "=============================================================================="
if timeout 1 bash -c "echo >/dev/tcp/${POSTGRES_HOST}/${POSTGRES_PORT}" 2>/dev/null; then
  echo "  PostgreSQL reachable at ${POSTGRES_HOST}:${POSTGRES_PORT}"
  record_pass "PostgreSQL connectivity" "reachable"
else
  echo "[FATAL] PostgreSQL not reachable at ${POSTGRES_HOST}:${POSTGRES_PORT}"
  echo "        Ensure the Docker container 'kestral-postgres' is running."
  exit 1
fi

# Verify we can query the database
if PGPASSWORD=localdev psql -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c "SELECT 1;" >/dev/null 2>&1; then
  record_pass "PostgreSQL query" "SELECT 1 succeeded"
else
  echo "[FATAL] Cannot execute query on PostgreSQL"
  exit 1
fi

# =============================================================================
# STEP 2: Start mcp-server as a local process
# =============================================================================
echo ""
echo "=============================================================================="
echo "[STEP 2/4] Starting mcp-server..."
echo "=============================================================================="

# Ensure we have a virtual environment with dependencies
if [ ! -d .venv ]; then
  echo "  Creating virtual environment..."
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt 2>/dev/null || true

export DATABASE_URL="${DATABASE_URL}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export PORT="${MCP_PORT}"

echo "  DATABASE_URL = postgresql://agentops:***@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
echo "  LOG_LEVEL    = ${LOG_LEVEL}"
echo "  PORT         = ${PORT}"
echo "  Log file     = /tmp/mcp-server.log"

python3 src/main.py > /tmp/mcp-server.log 2>&1 &
MCP_PID=$!
echo "  Process PID  = ${MCP_PID}"

# Wait for readiness
echo ""
echo "[STEP 3/4] Waiting for readiness..."
READY=0
for ((i=0; i<30; i++)); do
  if READYZ=$(curl -fsS --max-time 2 "${MCP_URL}/readyz" 2>/dev/null); then
    echo "  ${READYZ}"
    READY=1
    break
  fi
  sleep 1
done

if [[ ${READY} -eq 0 ]]; then
  echo "[FATAL] Server did not become ready within 30 seconds"
  tail -20 /tmp/mcp-server.log
  exit 1
fi

# Quick check that the process started and log is being written
sleep 1
if grep -q "Database pool ready" /tmp/mcp-server.log 2>/dev/null; then
  echo "  Database pool: ready"
else
  echo "  Database pool: NOT CONFIRMED — check /tmp/mcp-server.log"
fi

# =============================================================================
# STEP 4: Test all 9 tools (no search_policies)
# =============================================================================
echo ""
echo "=============================================================================="
echo "[STEP 4/4] Testing 9 MCP tools..."
echo "=============================================================================="

run_tool() {
  local tool="$1" desc="$2"
  shift 2
  echo ""
  echo "  --- ${tool} ---"
  echo "  Description: ${desc}"
  echo "  Arguments:   $*"

  local OUTPUT
  OUTPUT=$(fastmcp call "${MCP_HTTP}" "${tool}" "$@" 2>&1 | grep -v "UserWarning\|oauth.py\|site-packages\|/fastmcp/client/auth/" || true)

  echo "  Response:"
  echo "${OUTPUT}" | head -30 | sed 's/^/    /'

  # Simple validation: check for expected success indicators
  if echo "${OUTPUT}" | grep -qE '"result"|"eligible"|"status"|"transaction_id"|"ticket_id"|[a-f0-9]{8}-[a-f0-9]{4}'; then
    record_pass "${tool}" "Returned expected data"
  elif echo "${OUTPUT}" | grep -qi "error"; then
    record_fail "${tool}" "Tool returned an error — see response above"
  else
    record_fail "${tool}" "Unexpected response format — see response above"
  fi
}

# --- Read tools ---
run_tool "lookup_customer" \
  "Find customer by email" \
  email=priya.sharma@email.com run_id="${RUN_ID}"

run_tool "get_recent_orders" \
  "Return recent orders for user" \
  user_id=a1b2c3d4-e5f6-4a7b-8c9d-000000000001 run_id="${RUN_ID}"

run_tool "get_order_details" \
  "Return full order details" \
  order_id=c3d4e5f6-a7b8-4c9d-0e1f-000000000001 run_id="${RUN_ID}"

run_tool "check_refund_eligibility" \
  "Check refund eligibility" \
  order_id=c3d4e5f6-a7b8-4c9d-0e1f-000000000004 run_id="${RUN_ID}"

# --- Action tools ---
run_tool "issue_wallet_credit" \
  "Issue wallet credit" \
  user_id=a1b2c3d4-e5f6-4a7b-8c9d-000000000001 \
  amount=100.0 \
  reason="Test compensation" \
  run_id="${RUN_ID}"

run_tool "schedule_return_pickup" \
  "Schedule return pickup" \
  order_id=c3d4e5f6-a7b8-4c9d-0e1f-000000000001 \
  pickup_date="2026-06-01" \
  run_id="${RUN_ID}"

# --- Escalation tools ---
run_tool "create_ticket" \
  "Create support ticket" \
  user_id=a1b2c3d4-e5f6-4a7b-8c9d-000000000001 \
  query_text="Test ticket from local test" \
  classification='{"intent":"test","urgency":5,"sentiment":"neutral","auto_resolvable":true}' \
  priority=medium \
  assigned_team=general_support \
  run_id="${RUN_ID}"

run_tool "escalate_to_human" \
  "Escalate a ticket" \
  ticket_id=e5f6a7b8-c9d0-4e1f-2a3b-000000000001 \
  run_id="${RUN_ID}"

run_tool "route_to_team" \
  "Route ticket to team" \
  ticket_id=e5f6a7b8-c9d0-4e1f-2a3b-000000000001 \
  team=payments \
  run_id="${RUN_ID}"

# =============================================================================
# Verify tool count
# =============================================================================
echo ""
echo "--- Verifying registered tools ---"
TOOL_LIST=$(fastmcp list "${MCP_HTTP}" 2>&1 | grep -v "UserWarning\|oauth.py\|site-packages\|/fastmcp/client/auth/" || true)
echo "${TOOL_LIST}"
# To this (exclude the "Tools (9)" header line):
TOOL_COUNT=$(echo "${TOOL_LIST}" | grep -cE "^\s{2}[a-z_]+\(.*\)" || true)
echo "  Tools registered: ${TOOL_COUNT}"
if [[ "${TOOL_COUNT}" -eq 9 ]]; then
  record_pass "9 tools registered" "All 9 tools present"
else
  record_fail "9 tools registered" "Found ${TOOL_COUNT}, expected 9"
fi

# =============================================================================
# Check structured logging (correlation ID)
# =============================================================================
echo ""
echo "--- Checking structured logs for run_id: ${RUN_ID} ---"

LOG_LINES=$(grep "${RUN_ID}" /tmp/mcp-server.log 2>/dev/null || echo "")
if [[ -n "${LOG_LINES}" ]]; then
  LINE_COUNT=$(echo "${LOG_LINES}" | wc -l)
  echo "  Found ${LINE_COUNT} log lines with run_id"
  record_pass "Structured logging with run_id" "${LINE_COUNT} log lines"
else
  record_fail "Structured logging with run_id" "No log lines found — check /tmp/mcp-server.log"
fi

# =============================================================================
# Health endpoints
# =============================================================================
echo ""
echo "--- Checking health endpoints ---"
HEALTHZ=$(curl -fsS --max-time 2 "${MCP_URL}/healthz" 2>/dev/null || echo "FAIL")
READYZ=$(curl -fsS --max-time 2 "${MCP_URL}/readyz" 2>/dev/null || echo "FAIL")
echo "  GET /healthz : ${HEALTHZ}"
echo "  GET /readyz  : ${READYZ}"

if [[ "${HEALTHZ}" == "ok" ]]; then
  record_pass "Health endpoint /healthz" "Returned 'ok' (200)"
else
  record_fail "Health endpoint /healthz" "Got: ${HEALTHZ}"
fi
if [[ "${READYZ}" == "ready" ]]; then
  record_pass "Health endpoint /readyz" "Returned 'ready' (200)"
else
  record_fail "Health endpoint /readyz" "Got: ${READYZ}"
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "=============================================================================="
echo "  Summary"
echo "=============================================================================="
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
  echo "  mcp-server is working correctly — all checks passed."
  exit 0
else
  echo ""
  echo "  ${FAIL_COUNT} check(s) failed — review details above."
  echo ""
  echo "  Debugging tips:"
  echo "    Server logs : cat /tmp/mcp-server.log"
  echo "    Manual test : fastmcp call http://localhost:${MCP_PORT}/mcp lookup_customer email=priya.sharma@email.com"
  exit 1
fi