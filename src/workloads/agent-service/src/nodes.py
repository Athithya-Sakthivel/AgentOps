# =============================================================================
# nodes.py – Final demo agent (clean summaries, robust confirmations)
# =============================================================================
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
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


# ── Team routing ────────────────────────────────────────────────────
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
    "escalation": "service_center",
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


# ── Helper: filter orders by product mention ──────────────────────
def _filter_orders(orders: list[dict], query: str) -> list[dict]:
    query_lower = query.lower()
    words = [w for w in re.findall(r"\b[a-zA-Z]{3,}\b", query_lower)]
    if not words:
        return orders
    filtered = [o for o in orders if any(w in (o.get("product_name") or "").lower() for w in words)]
    return filtered if filtered else orders


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
        truly_unsafe = {"threat", "kill", "bomb", "hate", "suicide", "self‑harm", "violence"}
        if any(word in query.lower() for word in truly_unsafe):
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


# ── Helper: create ticket ──────────────────────────────────────────
async def _create_ticket_and_respond(
    state,
    mcp_client,
    run_id,
    summary,
    suggested_action,
    team,
    priority,
    sla,
    customer_name,
    classification_override=None,
):
    try:
        classification = classification_override or state.get("classification", {})
        raw = await mcp_client.call_tool(
            "create_ticket",
            {
                "user_id": state.get("user_id"),
                "query_text": state["query_text"],
                "classification": classification,
                "priority": priority,
                "assigned_team": team,
                "summary": summary[:500],
                "suggested_action": suggested_action,
            },
            run_id=run_id,
        )
        return {
            "final_response": f"{customer_name}, I've created a ticket for you. The {team.replace('_', ' ')} team will review it within {sla}.",
            "resolution_type": "auto_resolved",
            "ticket_id": str(raw) if raw else "unknown",
        }
    except Exception as exc:
        log_event("ERROR", "Ticket creation failed", run_id=run_id, error=str(exc))
        return {
            "final_response": "I'm sorry, I'm having trouble creating your ticket. A human agent will assist you shortly.",
            "resolution_type": "escalated",
        }


# ── 3. Conversational Agent ────────────────────────────────────────
SYSTEM_PROMPT = """You are a neutral support agent at Kestral. Answer concisely. Never mention a specific order when the customer asks a general policy question. Only mention an order when the customer asks about that specific product.

Tools: search_policies(query), create_ticket(summary, suggested_action, priority, assigned_team)

Rules:
- **Policy questions** (no specific product mentioned): Use search_policies and answer in general terms. Do NOT list orders.
- **Product‑specific questions**: Check the order data provided. It says if the return window is OPEN or CLOSED with the actual dates.
  * If CLOSED: "The return window closed on <date>. I can escalate if you believe there are extenuating circumstances."
  * If OPEN: "The return window is open until <date>. Would you like me to create a return request?"
- **Confirmation**: If the customer agrees ("yes", "ok", "go ahead", "escalate"), call create_ticket immediately. Use the last assistant message (provided below) to know what they're agreeing to.
- After creating a ticket, tell them the team and SLA. Never show the ticket ID.

JSON response: {"action": "tool_call", "tool": "<name>", "args": {<params>}} or {"action": "final_answer", "response": "<message>"}
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
    all_orders = customer_ctx.get("orders") or []
    all_orders = [o for o in all_orders if isinstance(o, dict)]

    # ── Filter orders relevant to the query ────────────────────────
    orders = _filter_orders(all_orders, query)

    # ── Pre‑calculated return windows (with dates for both OPEN and CLOSED) ──
    now = datetime.now(UTC)
    order_lines = []
    for o in orders[:5]:
        pname = o.get("product_name", "Unknown")
        delivery_str = o.get("delivery_date")
        window = o.get("return_window_days", 10)
        if delivery_str:
            try:
                delivery = datetime.fromisoformat(delivery_str.replace("Z", "+00:00"))
                closes = delivery + timedelta(days=window)
                if now > closes:
                    status = f"CLOSED (since {closes.strftime('%Y-%m-%d')})"
                else:
                    status = f"OPEN until {closes.strftime('%Y-%m-%d')}"
            except Exception:
                status = "unknown"
        else:
            status = "not delivered"
        order_lines.append(f"- {pname} (ID: {o['id'][:8]}) – return window {status}")
    orders_text = "\n".join(order_lines) if order_lines else "None"

    # ── Reliable last assistant message ───────────────────────────
    last_assistant = state.get("last_assistant_message", "")
    if not last_assistant:
        for msg in reversed(state.get("messages", [])):
            if hasattr(msg, "type") and msg.type == "ai":
                last_assistant = getattr(msg, "content", "")
                break
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                last_assistant = msg.get("content", "")
                break

    # ── Deterministic confirmation handler ─────────────────────────
    query_lower = query.lower().strip()
    confirmation_words = {"yes", "escalate", "ok", "okay", "proceed", "go ahead", "please"}
    is_confirmation = (
        query_lower in confirmation_words
        or query_lower.startswith("yes")
        or query_lower.startswith("escalate")
        or query_lower.startswith("ok")
    )
    if is_confirmation and last_assistant:
        last_lower = last_assistant.lower()

        mentioned_order = None
        for o in all_orders:
            if (
                o.get("id", "")[:8] in last_assistant
                or (o.get("product_name") or "").lower() in last_lower
            ):
                mentioned_order = o
                break

        if mentioned_order:
            product = mentioned_order.get("product_name", "your item")
            order_id = mentioned_order.get("id", "")[:8]

            # **Escalation confirmation** – clean summary, strip confirmation words
            if "escalat" in last_lower or "extenuating" in last_lower:
                # Strip confirmation words from the user's query to get the real reason
                reason = state["query_text"]
                confirmation_noise = {
                    "yes",
                    "escalate",
                    "ok",
                    "okay",
                    "please",
                    "proceed",
                    "go",
                    "ahead",
                    "my",
                    "as",
                }
                reason_parts = [w for w in reason.split() if w.lower() not in confirmation_noise]
                clean_reason = (
                    " ".join(reason_parts) if reason_parts else "Customer requested escalation"
                )
                summary = (
                    f"Customer escalated issue with {product} (order {order_id}). "
                    f"Reason: {clean_reason}. "
                    f"Last agent message: {last_assistant}"
                )
                suggested_action = "Investigate the issue and contact the customer."
                result = await _create_ticket_and_respond(
                    state,
                    mcp_client,
                    run_id,
                    summary,
                    suggested_action,
                    "service_center",
                    priority,
                    sla,
                    customer_name,
                    classification_override={"intent": "escalation"},
                )
                result["last_assistant_message"] = result["final_response"]
                return result

            # **Return confirmation**
            if "return" in last_lower and "would you like" in last_lower:
                summary = f"Customer confirmed return for {product} (order {order_id})."
                suggested_action = "Process return request and arrange pickup."
                result = await _create_ticket_and_respond(
                    state,
                    mcp_client,
                    run_id,
                    summary,
                    suggested_action,
                    "order_fulfillment",
                    priority,
                    sla,
                    customer_name,
                    classification_override={"intent": "return_request"},
                )
                result["last_assistant_message"] = result["final_response"]
                return result

        # Generic confirmation
        summary = f"Customer confirmed: {last_assistant}"
        suggested_action = "Review the issue and take appropriate action."
        result = await _create_ticket_and_respond(
            state, mcp_client, run_id, summary, suggested_action, team, priority, sla, customer_name
        )
        result["last_assistant_message"] = result["final_response"]
        return result

    # ── Build the LLM prompt ──────────────────────────────────────
    user_content = (
        f"Customer: {customer_name}\n"
        f"Query: {query}\n"
        f"Relevant orders:\n{orders_text}\n"
        f"Assigned team: {team}   Priority: {priority}   SLA: {sla}"
    )
    if last_assistant:
        user_content = f"LAST ASSISTANT MESSAGE:\n{last_assistant}\n\n" + user_content

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    for step in range(5):
        log_event("INFO", "Agent step", run_id=run_id, step=step)
        try:
            raw = await _call_llm_with_retry(resolver_lm, messages)
            raw_text = raw[0] if isinstance(raw, list) else raw
            result = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
        except (json.JSONDecodeError, KeyError):
            messages.append({"role": "user", "content": "Please respond with valid JSON."})
            continue

        action = result.get("action")
        if action == "final_answer":
            response = result.get("response", "I've noted your request.")
            return {
                "final_response": response,
                "resolution_type": "auto_resolved",
                "last_assistant_message": response,
            }

        tool_name = result.get("tool")
        tool_args = result.get("args", {})

        if tool_name == "search_policies":
            try:
                policy_results = await asyncio.to_thread(
                    search_policies, query=tool_args.get("query", query), top_k=3
                )
                tool_output = {"results": policy_results[:2]}
            except Exception as exc:
                tool_output = {"error": str(exc)}
        elif tool_name == "create_ticket":
            summary = tool_args.get("summary", query[:300])
            suggested_action = tool_args.get(
                "suggested_action", "Review the issue and take appropriate action."
            )
            final = await _create_ticket_and_respond(
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
            final["last_assistant_message"] = final["final_response"]
            return final
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


# ── 4. Human Escalate ─────────────────────────────────────────────
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
