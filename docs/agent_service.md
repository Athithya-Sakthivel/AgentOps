# Agent Service — Architecture & Implementation

## Overview

The agent‑service is the core AI agent in the AgentOps system. It receives customer messages via WebSocket, classifies them with a DSPy‑optimized model, gathers customer context from the MCP server, and autonomously resolves issues using a combination of deterministic dispatch and LLM‑driven reasoning. Unresolvable issues are escalated with full context to human teams.

**Stack:** FastAPI, LangGraph, DSPy, Bedrock (Llama 3 8B), PostgreSQL, S3, CloudWatch

---

## Architecture

```
WebSocket Message
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│                      LangGraph Agent                        │
│                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   │
│  │  guardrail   │───▶│   context    │───▶│   action     │   │
│  │  classifier  │    │  gatherer    │    │  dispatcher  │   │
│  └──────────────┘    └──────────────┘    └──────┬───────┘   │
│                                                 │           │
│                              ┌──────────────────┼──────┐    │
│                              │  action_taken?   │      │    │
│                              └──────┬───────────┘      │    │
│                              yes    │    no            │    │
│                              ┌──────▼──────┐  ┌───────▼──┐ │
│                              │  response   │  │ agentic  │ │
│                              │  formatter  │  │ resolver │ │
│                              └──────┬──────┘  └────┬─────┘ │
│                                     │              │       │
│                                     └──────┬───────┘       │
│                                            │               │
│                                     ┌──────▼──────┐        │
│                                     │  response   │        │
│                                     │  formatter  │        │
│                                     └──────┬──────┘        │
│                                            │               │
│  ┌──────────────┐                          │               │
│  │   human      │◄─────────────────────────┘               │
│  │  escalate    │  (if guardrail rejected                  │
│  └──────────────┘   or not auto‑resolvable)                │
│                                                             │
│  All state persisted to PostgreSQL via AsyncPostgresSaver   │
└─────────────────────────────────────────────────────────────┘
      │
      ▼
  WebSocket Response (JSON)
```

---

## Node Details

### 1. `guardrail_classifier`

**Purpose:** Classify every incoming message for safety, intent, urgency, sentiment, and auto‑resolvability before any tool is called or context is gathered.

**Implementation:**
- Uses a pre‑compiled DSPy `TriageProgram` loaded from `compiled/triage_program.json`
- The program was trained on 50 labeled examples using `BootstrapFewShot`
- Runtime inference uses Bedrock `meta.llama3-8b-instruct-v1:0` at temperature 0.0
- DSPy prediction is converted to a plain dict via `_prediction_to_dict()` for JSON serialization

**Output fields:**

| Field | Type | Description |
|-------|------|-------------|
| `safety` | `str` | `SAFE` or `UNSAFE` |
| `intent` | `str` | e.g. `late_delivery`, `wrong_item_delivered`, `refund_request`, `general_inquiry` |
| `urgency` | `int` | 1‑10 scale |
| `sentiment` | `str` | `angry`, `frustrated`, `confused`, `neutral`, `satisfied` |
| `auto_resolvable` | `bool` | Whether the system can resolve without human intervention |
| `required_action` | `str` | Human‑readable action description (from DSPy) |
| `required_tool` | `str` | MCP tool name suggested by DSPy |

**Routing:**
- `UNSAFE` → immediately routes to `human_escalate`
- `urgency >= 10` → immediately routes to `human_escalate`
- `auto_resolvable == false` → immediately routes to `human_escalate`
- All other cases → routes to `context_gatherer`

---

### 2. `context_gatherer`

**Purpose:** Fetch the customer profile and recent orders from the MCP server. This data is used by downstream nodes to ground tool calls in real data.

**Implementation:**
- Extracts email from the query using regex
- Calls `lookup_customer` via MCP to get customer profile
- Calls `get_recent_orders` via MCP to get last 5 orders
- Returns structured `customer_context` dict containing both

**Output:**
```python
{
    "user_id": "uuid",
    "customer_context": {
        "customer": { "id": "...", "full_name": "...", "segment": "..." },
        "orders": [ {...}, {...} ]
    }
}
```

If no email or user_id is present, skips context gathering and returns `customer_context: None`.

---

### 3. `action_dispatcher`

**Purpose:** Execute deterministic tool calls for known intent → action mappings, bypassing the LLM entirely for speed and reliability.

**Implementation:**
- Checks `auto_resolvable == True` and intent exists in `INTENT_TO_ACTION` mapping
- Builds tool arguments from templates, enriched with state data (user_id, order_id)
- For order‑related tools, resolves the best matching order using:
  1. Explicit order ID or tracking number in the query (`_extract_order_id_from_query`)
  2. Product name matching (`_resolve_order_id`)
  3. Fallback to most recent order
- For `issue_wallet_credit`, enforces the Rs.500 limit and sets default reason

**Intent → Action Mapping:**

| Intent | Tool | Pre‑check | Args Template |
|--------|------|-----------|---------------|
| `late_delivery` | `issue_wallet_credit` | No | `amount=100`, `reason="delivery delay compensation"` |
| `delayed_delivery` | `issue_wallet_credit` | No | Same as above |
| `damaged_product` | `schedule_return_pickup` | No | `pickup_date=<next business day>` |
| `defective_product` | `schedule_return_pickup` | No | Same as above |
| `return_request` | `check_refund_eligibility` | Yes | — |
| `refund_status` | `check_refund_eligibility` | Yes | — |
| `refund_query` | `check_refund_eligibility` | Yes | — |
| `refund_request` | `check_refund_eligibility` | Yes | — |
| `cancellation_request` | `check_refund_eligibility` | Yes | — |
| `wrong_item_delivered` | `check_refund_eligibility` | Yes | — |

**Output:**
- `action_taken: True` + `tool_results` if action executed
- `action_taken: False` if no mapping, not resolvable, or order_id cannot be resolved
- On explicit order ID mismatch → returns error result immediately (fake order rejection)

**Routing after dispatcher:**
- `action_taken == True` → routes to `response_formatter`
- `action_taken == False` → routes to `agentic_resolver`

---

### 4. `agentic_resolver`

**Purpose:** Handle queries that cannot be resolved deterministically. Uses an LLM with a strict tool‑first system prompt to decide which tools to call.

**Implementation:**
- Uses Bedrock `meta.llama3-8b-instruct-v1:0` at temperature 0.2
- Maximum 3 resolver steps
- The LLM is given the full customer context, recent orders, and classification
- Each step: LLM returns JSON with `action` (tool_call or final_answer), `tool`, `args`
- Order IDs are validated against the known orders list before any MCP call
- Policy search (`search_policies`) is called inline using S3 + Bedrock + NumPy

**System Prompt Highlights:**
- Strict decision tree forcing tool usage before text responses
- Critical rule: never invent order IDs — use only IDs from the provided orders list
- All amounts and dates must come from tool results, not guessed
- High‑value claims (>Rs.10,000) must be escalated

**Fallback behavior:**
- Bad JSON → forces a final answer
- Max steps reached → forces a final answer
- Tool call fails → error result is fed back to the LLM for recovery

---

### 5. `response_formatter`

**Purpose:** Convert raw tool results or LLM output into polished, customer‑facing messages. When tool results exist, the message is built from real data — never from raw LLM text.

**Implementation:**
- Checks for `error` key first — surfaces errors clearly
- Tool‑specific templates for `issue_wallet_credit`, `check_refund_eligibility`, `schedule_return_pickup`
- Each template uses actual values from the tool result (amount, transaction ID, dates, reasons)
- Unknown tools get a generic success message
- If no tool results, falls back to the LLM's `final_response`

**Template Examples:**

| Tool Result | Formatted Message |
|-------------|-------------------|
| `{"status":"issued","amount":100,"transaction_id":"WC-abc123"}` | "I've issued Rs.100 as store credit (transaction #WC-abc123)..." |
| `{"eligible":false,"reason":"return_window_expired (10 days)"}` | "I checked your refund eligibility: return_window_expired (10 days)..." |
| `{"status":"scheduled","pickup_date":"2026-06-01"}` | "A return pickup has been scheduled for 2026-06-01..." |
| `{"error":"Order ORD-99999 not found..."}` | "Order ORD-99999 not found in your recent orders." |

---

### 6. `human_escalate`

**Purpose:** Create a support ticket and route it to the appropriate team when the agent cannot resolve the issue.

**Implementation:**
- Determines priority from urgency: ≥9 → critical, ≥7 → high, else medium
- Maps intent to team using `TEAM_ROUTING` dict
- Calls `create_ticket` via MCP with full classification, priority, and team assignment
- Returns a formatted response with ticket ID and SLA

**Team Routing:**

| Intent | Team |
|--------|------|
| `wrong_item_delivered` | order_fulfillment |
| `damaged_product` | service_center |
| `late_delivery` | logistics |
| `refund_status` | payments |
| `warranty_claim` | service_center |
| `payment_issue` | payments |
| `account_issue` | senior_support |
| `complaint` | senior_support |
| `general_inquiry` | general_support |

---

## Multi‑Turn Conversation Support

**Configuration knob:** `multi_turn_enabled` (default: `True`)

**How it works:**
1. Each WebSocket connection has a `session_id` used as the LangGraph `thread_id`
2. Before processing a new message, `routes.py` calls `graph.aget_state()` to load the previous checkpoint from PostgreSQL
3. If a previous state exists, the new message is appended to the existing message history
4. Customer context (profile, orders) is preserved across turns
5. Turn limit: `max_conversation_turns` (default: 20) prevents unbounded growth

**What this enables:**
- Customer says "I'm Priya Sharma" in turn 1
- Turn 2 asks about a policy — agent still knows it's Priya
- Turn 3 asks "check my Samsung order" — agent uses the orders loaded in turn 1

---

## Order ID Validation (Anti‑Hallucination)

Multiple layers prevent the agent from acting on non‑existent orders:

1. **`action_dispatcher`** — Extracts order IDs from the query, validates against the known orders list. Rejects immediately if not found.
2. **`agentic_resolver`** — Validates every `order_id` the LLM proposes against the known orders list. Injects an error result if the ID is not found.
3. **`RESOLVER_SYSTEM_PROMPT`** — Explicitly instructs the LLM to use only IDs from the provided orders list.

---

## State Management

**State type:** `AgentState` (extends `MessagesState`)

| Field | Type | Description |
|-------|------|-------------|
| `messages` | `list[BaseMessage]` | Full conversation history (auto‑appended via `add_messages` reducer) |
| `query_text` | `str` | Current user query |
| `user_id` | `str | None` | Customer UUID |
| `thread_id` | `str` | Conversation thread (session) ID |
| `run_id` | `str` | Correlation ID for logging |
| `guardrail_rejected` | `bool` | Whether the safety check failed |
| `classification` | `dict | None` | DSPy triage output |
| `customer_context` | `dict | None` | Customer profile + recent orders |
| `action_taken` | `bool` | Whether dispatcher executed a tool |
| `tool_results` | `list[dict]` | Results of all tool calls |
| `resolution_type` | `str | None` | `auto_resolved` or `escalated` |
| `ticket_id` | `str | None` | Ticket UUID if escalated |
| `final_response` | `str | None` | Final message to customer |
| `error` | `str | None` | Error message if any |

**Persistence:** All state is checkpointed to PostgreSQL via `AsyncPostgresSaver` after every node execution. Checkpoints are loaded on subsequent turns for multi‑turn conversations.

---

## Runtime Dependencies

Passed via `Context` dataclass (injected through `graph.ainvoke(..., context=ctx)`):

| Dependency | Type | Purpose |
|------------|------|---------|
| `triage_program` | `dspy.Module` | Pre‑compiled DSPy classifier |
| `mcp_client` | `MCPClientManager` | FastMCP client for tool calls |
| `resolver_lm` | `dspy.LM` | Bedrock LLM for agentic resolver |

---

## Configuration Knobs

| Knob | Type | Default | Description |
|------|------|---------|-------------|
| `llm_safeguard_model` | `str` | `bedrock/meta.llama3-8b-instruct-v1:0` | Model for DSPy triage |
| `llm_resolver_model` | `str` | `bedrock/meta.llama3-8b-instruct-v1:0` | Model for agentic resolver |
| `urgency_escalate_threshold` | `int` | `8` | Urgency level that triggers immediate escalation |
| `max_auto_resolve_amount` | `float` | `10000.00` | Maximum order value for auto‑resolution |
| `max_wallet_credit_amount` | `float` | `500.00` | Maximum wallet credit per transaction |
| `multi_turn_enabled` | `bool` | `True` | Enable multi‑turn conversation memory |
| `max_conversation_turns` | `int` | `20` | Maximum turns before starting fresh |
| `mcp_server_url` | `str` | `http://localhost:8001/mcp` | MCP server endpoint |
| `embeddings_bucket` | `str` | `agentops-embeddings-temp-xyz` | S3 bucket for policy embeddings |

---

## Policy Search (Inline RAG)

Policy search is not an MCP tool. It runs inline in the agent‑service using:

- **Amazon Bedrock Titan Embeddings v2** — generates 1024‑dim query vectors
- **S3** — stores pre‑computed policy embeddings as a single JSON file (~1.3 MB, 59 chunks)
- **NumPy** — brute‑force cosine similarity in‑process (<5ms for <1,000 chunks)
- **In‑memory cache** — embeddings loaded once at startup via `warmup_cache_async()`

This eliminates the vector database (Qdrant/OpenSearch) and the separate dense‑embedding service. See ADR 004 for rationale.

---

## Observability

All services emit structured JSON logs to stderr, captured by CloudWatch:

```json
{
  "timestamp": "2026-05-30T11:33:45.018822+00:00",
  "level": "INFO",
  "message": "Node started",
  "run_id": "a3d80a7b-2942-49d7-9119-1a9d002cd415",
  "node": "action_dispatcher",
  "intent": "delayed_delivery"
}
```

**Key observability features:**
- **Correlation ID** (`run_id`): Generated per WebSocket message, propagated to all MCP tool calls
- **Node lifecycle logging**: Every node logs start/completion with timing
- **Tool call tracing**: Every MCP tool call logs start/success/failure with duration
- **CloudWatch Logs Insights**: Query by `run_id` to reconstruct full request path across services
- **Custom metrics**: `ChatSessionsActive`, `TicketsCreated`, `AdminOverrides`
- **Alarms**: `TicketsCreatedHigh`, `ServiceUnhealthy`