# =============================================================================
# nodes.py – Conversational LLM agent with tools
# =============================================================================
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from langgraph.runtime import Runtime
from logging_utils import log_event
from policy_search import search_policies
from state import AgentState, Context

log = logging.getLogger("agent-service")


def _prediction_to_dict(pred: Any) -> dict[str, Any]:
    if pred is None:
        return {}
    if isinstance(pred, dict):
        return pred
    return {k: v for k, v in pred.items() if not k.startswith("_")}


def _safe_json_dumps(obj: Any) -> str:
    if obj is None:
        return "null"
    if isinstance(obj, dict):
        return json.dumps({k: v for k, v in obj.items() if not k.startswith("_")})
    try:
        return json.dumps(obj)
    except (TypeError, ValueError):
        return json.dumps(str(obj))


def _safe_extract_data(raw_result: Any) -> Any:
    if raw_result is None:
        return None
    if hasattr(raw_result, "data") and raw_result.data is not None:
        return raw_result.data
    if isinstance(raw_result, dict) and "result" in raw_result and len(raw_result) == 1:
        return raw_result["result"]
    return raw_result


# ── Team routing (deterministic, used only for ticket creation) ──────
INTENT_TO_TEAM: dict[str, str] = {
    "return_request": "order_fulfillment",
    "refund_status": "payments",
    "cancellation_request": "order_fulfillment",
    "wrong_item_delivered": "order_fulfillment",
    "damaged_product": "service_center",
    "defective_product": "service_center",
    "late_delivery": "logistics",
    "delivery_issue": "logistics",
    "payment_issue": "payments",
    "account_issue": "senior_support",
    "complaint": "senior_support",
    "general_inquiry": "general_support",
}


def _default_team(intent: str) -> str:
    return INTENT_TO_TEAM.get(intent, "general_support")


def _get_priority_and_sla(urgency: int) -> tuple[str, str]:
    if urgency >= 9:
        return "critical", "2 hours"
    if urgency >= 7:
        return "high", "4 hours"
    return "medium", "24 hours"


# ── 1. Guardrail ────────────────────────────────────────────────────
async def guardrail_classifier(state: AgentState, runtime: Runtime[Context]) -> dict[str, Any]:
    triage_program = runtime.context.triage_program
    run_id = state.get("run_id", "")
    log_event("INFO", "Node started", node="guardrail_classifier", run_id=run_id)

    query = state["query_text"].strip()
    if not query or len(query) < 3:
        return {
            "guardrail_rejected": True,
            "final_response": "I'm sorry, I didn't catch that. Could you rephrase?",
            "resolution_type": "escalated",
        }

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, triage_program, query)
    except Exception:
        log_event("ERROR", "Triage program failed", run_id=run_id)
        raise

    classification = _prediction_to_dict(result)
    log_event(
        "INFO",
        "Triage completed",
        run_id=run_id,
        safety=classification.get("safety", "UNKNOWN"),
        intent=classification.get("intent", "unknown"),
    )

    if classification.get("safety") == "UNSAFE":
        return {
            "guardrail_rejected": True,
            "classification": classification,
            "final_response": "Your message has been flagged for review. A human agent will assist you shortly.",
            "resolution_type": "escalated",
        }

    return {"guardrail_rejected": False, "classification": classification}


# ── 2. Context Gatherer ─────────────────────────────────────────────
async def context_gatherer(state: AgentState, runtime: Runtime[Context]) -> dict[str, Any]:
    mcp_client = runtime.context.mcp_client
    run_id = state.get("run_id", "")
    log_event("INFO", "Node started", node="context_gatherer", run_id=run_id)

    query = state["query_text"]
    email_match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", query)
    email = email_match.group(0) if email_match else state.get("user_email")
    user_id = state.get("user_id")

    if not email and not user_id:
        log_event("WARN", "No customer identifier - skipping context", run_id=run_id)
        return {"customer_context": None}

    customer = None
    if email:
        raw = await mcp_client.call_tool("lookup_customer", {"email": email}, run_id=run_id)
        raw = _safe_extract_data(raw)
        if isinstance(raw, dict):
            customer = raw
        elif isinstance(raw, list) and raw:
            customer = raw[0] if isinstance(raw[0], dict) else None

    if customer and isinstance(customer, dict) and customer.get("id"):
        user_id = customer["id"]
    if user_id and isinstance(user_id, str) and user_id.startswith("google#"):
        return {"user_id": user_id, "user_email": email, "customer_context": None}

    orders = []
    if user_id and not (isinstance(user_id, str) and "#" in user_id):
        raw = await mcp_client.call_tool("get_recent_orders", {"user_id": user_id}, run_id=run_id)
        raw = _safe_extract_data(raw)
        if isinstance(raw, list):
            orders = [o for o in raw if isinstance(o, dict)]
        elif isinstance(raw, dict):
            orders = [raw] if raw else []

    log_event(
        "INFO",
        "Context gathered",
        run_id=run_id,
        customer_found=customer is not None,
        orders_count=len(orders),
    )
    return {
        "user_id": user_id,
        "user_email": email,
        "customer_context": {"customer": customer, "orders": orders},
    }


# ── Helper: create ticket and return confirmation (no ID in chat) ────
async def _create_ticket_and_respond(
    state, mcp_client, run_id, summary, suggested_action, team, priority, sla, customer_name
):
    try:
        raw = await mcp_client.call_tool(
            "create_ticket",
            {
                "user_id": state.get("user_id"),
                "query_text": state["query_text"],
                "classification": state.get("classification", {}),
                "priority": priority,
                "assigned_team": team,
                "summary": summary[:500],
                "suggested_action": suggested_action,
            },
            run_id=run_id,
        )
        ticket_id = _safe_extract_data(raw)
        ticket_id_str = str(ticket_id) if ticket_id else "unknown"
        # Do not reveal the full UUID; just confirm
        return {
            "final_response": f"{customer_name}, I've created a ticket for you. The {team.replace('_', ' ')} team will review it within {sla}.",
            "resolution_type": "auto_resolved",
            "ticket_id": ticket_id_str,
        }
    except Exception as exc:
        log_event("ERROR", "Ticket creation failed", run_id=run_id, error=str(exc))
        return {
            "final_response": "I'm sorry, I'm having trouble creating your ticket. A human agent will assist you shortly.",
            "resolution_type": "escalated",
        }


# ── 3. Conversational Agent (LLM with tools) ─────────────────────────
SYSTEM_PROMPT = """You are a helpful, empathetic customer support agent for Kestral, an Indian e-commerce company. You have access to the following tools:

- `get_recent_orders` : returns a list of the customer's recent orders with product names, delivery dates, return windows, and statuses. Use this to answer questions about orders.
- `search_policies(query: str)` : search internal policy documents. Use this to answer policy questions.
- `create_ticket(summary: str, suggested_action: str, priority: str, assigned_team: str)` : create a support ticket for the customer. You MUST ONLY call this after the customer has explicitly asked you to proceed (for example, after you have explained their return eligibility and they say "yes, create a ticket").

**Conversation rules:**
- Always greet the customer by name.
- If a customer has a problem but is vague (e.g., "I have a problem with my order"), ask them to describe the specific issue and which product it is about. Do not create a ticket until the issue is clear and the customer agrees.
- If a customer asks about return eligibility, use `get_recent_orders` to retrieve their orders, then calculate whether the return window is still open. Tell the customer clearly: "Your <product> (order <short_id>) was delivered on <date>. The <N>-day return window <is still open / closed on <date>>. <If still open: Would you like me to create a return request for you? / If closed: Unfortunately it cannot be returned. I can escalate if you believe there are extenuating circumstances.>"
- If a customer reports a concrete problem (damaged product, wrong item, late delivery), ask for any missing details, then offer to create a ticket. Wait for the customer to say "yes" before calling `create_ticket`.
- When you create a ticket, you must include a `summary` (2-3 sentences describing the issue, including product and order ID) and a `suggested_action` (one sentence for the support team). The `priority` should be based on urgency (critical/high/medium) and `assigned_team` should be the appropriate team (e.g., logistics, service_center, order_fulfillment).
- After creating a ticket, tell the customer which team will handle it and the expected response time. DO NOT display the ticket ID – just say "Your ticket has been created."
- If a customer asks about policies, use `search_policies` and answer concisely.

**Important:** Never guess order details. Use only the data returned by `get_recent_orders`.

Respond in JSON format:
- To call a tool: {"action": "tool_call", "tool": "<name>", "args": {<params>}}
- To reply to the customer: {"action": "final_answer", "response": "<your message>"}
"""


async def _call_llm_with_retry(lm, messages, max_retries=3):
    for attempt in range(max_retries):
        try:
            return await lm.acall(messages=messages)
        except Exception as e:
            if "429" in str(e) or "Too many requests" in str(e):
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2**attempt)
            else:
                raise


async def conversational_agent(state: AgentState, runtime: Runtime[Context]) -> dict[str, Any]:
    resolver_lm = runtime.context.resolver_lm
    mcp_client = runtime.context.mcp_client
    run_id = state.get("run_id", "")
    log_event("INFO", "Node started", node="conversational_agent", run_id=run_id)

    query = state["query_text"]
    classification = state.get("classification", {})
    intent = classification.get("intent", "general_inquiry")
    urgency = classification.get("urgency", 0)
    priority, sla = _get_priority_and_sla(urgency)
    team = _default_team(intent)

    customer_ctx = state.get("customer_context") or {}
    customer = customer_ctx.get("customer") or {}
    if not isinstance(customer, dict):
        customer = {}
    customer_name = customer.get("full_name", "Hello")
    orders = customer_ctx.get("orders") or []
    orders = [o for o in orders if isinstance(o, dict)]

    # Build a compact, data‑rich prompt
    order_lines = []
    for o in orders[:5]:
        pname = o.get("product_name", "Unknown")
        delivery = (o.get("delivery_date") or "not delivered")[:10]
        window = o.get("return_window_days", "N/A")
        order_lines.append(
            f"- {pname} (ID: {o['id'][:8]}) delivered {delivery}, {window}-day return window"
        )
    orders_text = "\n".join(order_lines) if order_lines else "None"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Customer: {customer_name}\n"
                f"Query: {query}\n"
                f"Recent orders:\n{orders_text}\n"
                f"Assigned team: {team}   Priority: {priority}   SLA: {sla}"
            ),
        },
    ]

    for step in range(5):
        log_event("INFO", "Agent step", run_id=run_id, step=step)
        try:
            raw = await _call_llm_with_retry(resolver_lm, messages)
            raw_text = raw[0] if isinstance(raw, list) else raw
            result = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
        except (json.JSONDecodeError, KeyError):
            messages.append(
                {
                    "role": "user",
                    "content": "Please respond with valid JSON (action: tool_call or final_answer).",
                }
            )
            continue

        action = result.get("action")
        if action == "final_answer":
            response = result.get("response", "I've noted your request.")
            return {"final_response": response, "resolution_type": "auto_resolved"}

        tool_name = result.get("tool")
        tool_args = result.get("args", {})

        if tool_name == "get_recent_orders":
            # Already have orders in context, but the LLM may request a refresh
            tool_output = orders
        elif tool_name == "search_policies":
            try:
                policy_results = await asyncio.to_thread(
                    search_policies, query=tool_args.get("query", query), top_k=3
                )
                tool_output = {"results": policy_results[:2]}
            except Exception as exc:
                tool_output = {"error": str(exc)}
        elif tool_name == "create_ticket":
            # The LLM has decided to create a ticket – execute it
            summary = tool_args.get("summary", query[:300])
            suggested_action = tool_args.get(
                "suggested_action", "Review the issue and take appropriate action."
            )
            return await _create_ticket_and_respond(
                state,
                mcp_client,
                run_id,
                summary,
                suggested_action,
                team,
                priority,
                sla,
                customer_name,
            )
        else:
            tool_output = {"error": f"Unknown tool: {tool_name}"}

        messages.append({"role": "assistant", "content": json.dumps(result)})
        messages.append(
            {"role": "user", "content": f"Tool result: {_safe_json_dumps(tool_output)[:300]}"}
        )

    return {
        "final_response": "I'm sorry, I'm having trouble processing your request. A human agent will assist you shortly.",
        "resolution_type": "escalated",
    }


# ── 4. Human Escalate (unsafe content only) ─────────────────────────
async def human_escalate(state: AgentState, runtime: Runtime[Context]) -> dict[str, Any]:
    mcp_client = runtime.context.mcp_client
    run_id = state.get("run_id", "")

    classification = state.get("classification", {})
    intent = classification.get("intent", "general_inquiry")
    urgency = classification.get("urgency", 5)
    priority, sla = _get_priority_and_sla(urgency)
    team = _default_team(intent)

    customer_ctx = state.get("customer_context") or {}
    customer = customer_ctx.get("customer") or {}
    if not isinstance(customer, dict):
        customer = {}
    customer_name = customer.get("full_name", "Hello")

    return await _create_ticket_and_respond(
        state,
        mcp_client,
        run_id,
        f"[UNSAFE] {state['query_text'][:200]}",
        "Review flagged content immediately.",
        team,
        priority,
        sla,
        customer_name,
    )
