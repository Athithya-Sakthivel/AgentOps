# Agent Service — Architecture & Implementation (Pragmatic Triage Agent)

## Overview

The agent‑service is the triage and enrichment layer of the AgentOps system. It receives customer messages via WebSocket, classifies them with a DSPy‑optimized model, gathers customer context from the MCP server, then uses an LLM‑powered **ticket router** to either answer the customer directly (via policy RAG) or create a richly‑detailed ticket routed to the correct team. All decisions that affect the customer (refunds, credits, pickups) are left to human agents – the system’s job is to make those agents **faster and more accurate**.

**Stack:** FastAPI, LangGraph, DSPy, Bedrock (Llama 3 8B), PostgreSQL, S3, CloudWatch

---

## Architecture

```
WebSocket Message
      │
      ▼
┌──────────────────────────────────────────────────────┐
│                   LangGraph Agent                    │
│                                                      │
│  ┌──────────────┐    ┌──────────────┐               │
│  │  guardrail   │───▶│   context    │               │
│  │  classifier  │    │  gatherer    │               │
│  └──────┬───────┘    └──────┬───────┘               │
│         │                   │                       │
│         │ unsafe / urgent   │                       │
│         ▼                   ▼                       │
│  ┌──────────────┐    ┌──────────────┐               │
│  │   human      │    │   ticket     │               │
│  │  escalate    │    │   router     │  (LLM + tools)│
│  └──────────────┘    └──────┬───────┘               │
│                             │                       │
│                             ▼                       │
│                        Final Response               │
│                                                      │
│  State persisted via AsyncPostgresSaver              │
└──────────────────────────────────────────────────────┘
      │
      ▼
  WebSocket Response (JSON)
```

---

## Node Details

### 1. `guardrail_classifier`

**Purpose:** Classify every incoming message for safety, intent, urgency, and sentiment before context is gathered or any tool is called.

**Implementation:**
- Uses a pre‑compiled DSPy `TriageProgram` loaded from `compiled/triage_program.json`
- The program was trained on 50 labeled examples using `BootstrapFewShot`
- Runtime inference uses Bedrock `meta.llama3-8b-instruct-v1:0` at temperature 0.0

**Output fields:**

| Field | Type | Description |
|-------|------|-------------|
| `safety` | `str` | `SAFE` or `UNSAFE` |
| `intent` | `str` | e.g. `wrong_item_delivered`, `return_request`, `general_inquiry` |
| `urgency` | `int` | 1‑10 scale |
| `sentiment` | `str` | `angry`, `frustrated`, `confused`, `neutral`, `satisfied` |
| `auto_resolvable` | `bool` | Always `false` for actionable issues; only `true` for pure policy questions |

**Routing:**
- `UNSAFE` or `urgency >= 10` → immediately routes to `human_escalate`
- All other cases → routes to `context_gatherer`

---

### 2. `context_gatherer`

**Purpose:** Fetch the customer profile and recent orders from the MCP server. This data enriches the ticket that the `ticket_router` creates.

**Implementation:**
- Extracts email from the query using regex, or uses the JWT‑derived `user_email` from the WebSocket session
- Calls `lookup_customer` via MCP to get customer profile
- Calls `get_recent_orders` via MCP to get the last 5 orders (with product names)
- Returns structured `customer_context` dict

**Output:**
```python
{
    "user_id": "uuid",
    "user_email": "customer@example.com",
    "customer_context": {
        "customer": { "id": "...", "full_name": "...", "segment": "..." },
        "orders": [ {...}, {...} ]
    }
}
```

If no email or user_id is present, context gathering is skipped.

---

### 3. `ticket_router` (LLM with tools)

**Purpose:** The core decision node. Based on the customer’s query, classification, and gathered context, the LLM either answers the customer directly (using policy RAG) or creates a support ticket with an AI‑written summary, suggested action, and deterministic team routing.

**Tools available to the LLM:**
- `search_policies(query)` – searches internal policy documents (inline RAG using Bedrock Titan + S3 + NumPy)
- `create_ticket(user_id, query_text, classification, priority, assigned_team, summary, suggested_action)` – creates a ticket in PostgreSQL via the MCP server

**Deterministic safeguards (enforced by the agent, not the LLM):**
- **Team assignment** is always derived from the DSPy intent using a hard‑coded `INTENT_TO_TEAM` mapping.
- **Priority** is calculated from the urgency score: ≥9 → `critical`, ≥7 → `high`, else `medium`.
- **Summary** is written by the LLM but must include specific order IDs and product names when applicable.
- **Suggested action** is written by the LLM as a one‑line recommendation for the human resolver.

**System Prompt Highlights:**
- Always use the customer’s name.
- Answer simple policy questions directly using `search_policies` – do not create a ticket.
- Only create a ticket when human action is required (return, refund, complaint, investigation).
- When creating a ticket, the summary must include specific order IDs and product names from the “Recent orders” list.
- Never promise a refund, credit, or pickup – only assure the customer that the right team will assist.

**Fallback behavior:**
- Max 3 reasoning steps.
- Bad JSON or missing fields → an error message is fed back to the LLM for recovery.
- If the LLM fails to produce a valid response after max steps, a generic escalation message is returned.

---

### 4. `human_escalate` (for unsafe / urgent messages)

**Purpose:** Immediately create a high‑priority ticket when the guardrail rejects a message or urgency is extreme. Skips the LLM entirely for speed and safety.

**Implementation:**
- Uses the same `INTENT_TO_TEAM` mapping and urgency‑based priority.
- Calls `create_ticket` via MCP directly, with the original query as the summary.

---

## Team Routing (Deterministic)

Routing is handled by a simple mapping, not the LLM, ensuring 100% predictability.

| Intent | Team |
|--------|------|
| `return_request` | order_fulfillment |
| `refund_status` | payments |
| `cancellation_request` | order_fulfillment |
| `wrong_item_delivered` | order_fulfillment |
| `damaged_product` | service_center |
| `defective_product` | service_center |
| `late_delivery` | logistics |
| `delivery_issue` | logistics |
| `payment_issue` | payments |
| `account_issue` | senior_support |
| `complaint` | senior_support |
| `general_inquiry` | general_support |

Any intent not in the table defaults to `general_support`.

---

## State Management

**State type:** `AgentState` (extends `MessagesState`)

| Field | Type | Description |
|-------|------|-------------|
| `messages` | `list[BaseMessage]` | Full conversation history |
| `query_text` | `str` | Current user query |
| `user_id` | `str | None` | Customer UUID |
| `user_email` | `str | None` | Email from JWT |
| `thread_id` | `str` | Conversation thread ID |
| `run_id` | `str` | Correlation ID for logging |
| `guardrail_rejected` | `bool` | Whether the safety check failed |
| `classification` | `dict | None` | DSPy triage output |
| `customer_context` | `dict | None` | Customer profile + recent orders |
| `resolution_type` | `str | None` | `auto_resolved` or `escalated` |
| `ticket_id` | `str | None` | Ticket UUID if created |
| `final_response` | `str | None` | Final message to customer |
| `error` | `str | None` | Error message if any |

**Persistence:** All state is checkpointed to PostgreSQL via `AsyncPostgresSaver` after every node execution.

---

## Runtime Dependencies

Passed via `Context` dataclass:

| Dependency | Type | Purpose |
|------------|------|---------|
| `triage_program` | `dspy.Module` | Pre‑compiled DSPy classifier |
| `mcp_client` | `MCPClientManager` | FastMCP client for `lookup_customer`, `get_recent_orders`, `create_ticket` |
| `resolver_lm` | `dspy.LM` | Bedrock LLM for the `ticket_router` |

---

## Policy Search (Inline RAG)

Policy search runs inline in the agent‑service using:

- **Amazon Bedrock Titan Embeddings v2** — generates 1024‑dim query vectors
- **S3** — stores pre‑computed policy embeddings as a single JSON file (~1.3 MB, 59 chunks)
- **NumPy** — brute‑force cosine similarity in‑process (<5ms for <1,000 chunks)
- **In‑memory cache** — embeddings loaded once at startup via `warmup_cache_async()`

This eliminates the need for a separate vector database.

---

## Observability

All services emit structured JSON logs to stderr, captured by CloudWatch:

```json
{
  "timestamp": "2026-06-01T11:03:45.018822+00:00",
  "level": "INFO",
  "message": "Node started",
  "run_id": "a3d80a7b-2942-49d7-9119-1a9d002cd415",
  "node": "ticket_router",
  "intent": "wrong_item_delivered"
}
```

**Key observability features:**
- **Correlation ID** (`run_id`): Generated per WebSocket message, propagated to all MCP tool calls
- **Node lifecycle logging**: Every node logs start/completion
- **Tool call tracing**: Every MCP tool call logs start/success/failure
- **CloudWatch Logs Insights**: Query by `run_id` to reconstruct full request path

---

## What Makes This Different from Traditional Systems

| Traditional (keyword / rule‑based) | AgentOps |
|-----------------------------------|----------|
| Matches keywords → often misroutes | Understands intent via DSPy + LLM → accurate routing |
| No context – agent must look up everything | Pre‑fetched customer profile, orders, and policy snippets |
| No ticket summary – agent reads full chat | AI‑written 2‑3 sentence summary with order IDs and suggested action |
| One ticket = one issue | Multi‑issue queries consolidated into a single structured ticket |
| Rigid, hard to update | DSPy programs can be re‑compiled from new examples |
