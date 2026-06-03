# =============================================================================
# nodes.py - Autonomous Ticket Triage Agent
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


# ── Ticket creation helper ──────────────────────────────────────────
async def _create_ticket(
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
        return {
            "final_response": f"{customer_name}, I've created a ticket for your issue. The {team.replace('_', ' ')} team will review it within {sla}. Your reference number is {ticket_id_str}.",
            "resolution_type": "auto_resolved",
            "ticket_id": ticket_id_str,
        }
    except Exception as exc:
        log_event("ERROR", "Ticket creation failed", run_id=run_id, error=str(exc))
        return {
            "final_response": "I'm sorry, I'm having trouble creating your ticket. A human agent will assist you shortly.",
            "resolution_type": "escalated",
        }


# ── 3. Intent Router ────────────────────────────────────────────────
async def intent_router(state: AgentState, runtime: Runtime[Context]) -> dict[str, Any]:
    classification = state.get("classification", {})
    intent = classification.get("intent", "general_inquiry")
    urgency = classification.get("urgency", 0)
    priority, sla = _get_priority_and_sla(urgency)

    query_lower = state["query_text"].lower()
    policy_keywords = {
        "policy",
        "return policy",
        "refund policy",
        "how long",
        "shipping",
        "delivery time",
        "warranty",
        "exchange policy",
    }

    if intent == "general_inquiry" and any(kw in query_lower for kw in policy_keywords):
        return await policy_qa(state, runtime)

    return await ticket_writer(state, runtime)


# ── 4. Policy Q&A ───────────────────────────────────────────────────
async def policy_qa(state: AgentState, runtime: Runtime[Context]) -> dict[str, Any]:
    resolver_lm = runtime.context.resolver_lm
    run_id = state.get("run_id", "")
    log_event("INFO", "Node started", node="policy_qa", run_id=run_id)

    query = state["query_text"]
    customer_name = "Hello"
    ctx = state.get("customer_context") or {}
    cust = ctx.get("customer")
    if isinstance(cust, dict):
        customer_name = cust.get("full_name", customer_name)

    policy_chunks = []
    try:
        results = await asyncio.to_thread(search_policies, query=query, top_k=3)
        policy_chunks = results[:2]
    except Exception:
        pass

    policy_text = ""
    for chunk in policy_chunks:
        policy_text += f"**{chunk.get('chunk_title', '')}**\n{chunk.get('chunk_text', '')}\n\n"

    system_prompt = "You are a helpful support agent. Answer the customer's policy question using ONLY the provided policy text. Be concise and friendly."
    user_prompt = f"Customer: {customer_name}\nQuestion: {query}\n\nRelevant policies:\n{policy_text if policy_text else 'No specific policy found.'}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    try:
        raw = await resolver_lm.acall(messages=messages)
        raw_text = raw[0] if isinstance(raw, list) else raw
        response = (
            raw_text if isinstance(raw_text, str) else raw_text.get("response", str(raw_text))
        )
    except Exception:
        response = "I'm sorry, I couldn't retrieve our policy documents at the moment."

    return {"final_response": response, "resolution_type": "auto_resolved"}


# ── 5. Ticket Writer ────────────────────────────────────────────────
TICKET_WRITER_SYSTEM_PROMPT = """You are an expert support ticket writer. Given a customer's query and their recent orders, write a concise ticket summary (2-3 sentences) and a one-sentence suggested action for the support agent.

The summary MUST include:
- The customer's name
- Specific order IDs and product names if mentioned or clearly implied
- What the customer reported

The suggested action should be a clear next step for the support team.

Output as JSON:
{"summary": "...", "suggested_action": "..."}
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


async def ticket_writer(state: AgentState, runtime: Runtime[Context]) -> dict[str, Any]:
    resolver_lm = runtime.context.resolver_lm
    mcp_client = runtime.context.mcp_client
    run_id = state.get("run_id", "")
    log_event("INFO", "Node started", node="ticket_writer", run_id=run_id)

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

    order_lines = []
    for o in orders[:3]:
        pname = o.get("product_name", "Unknown")
        delivery = (o.get("delivery_date") or "N/A")[:10]
        order_lines.append(f"- {pname} (ID: {o['id'][:8]}, delivered: {delivery})")
    orders_text = "\n".join(order_lines) if order_lines else "None"

    user_prompt = f"Customer: {customer_name}\nQuery: {query}\nRecent orders:\n{orders_text}\nAssigned team: {team}, Priority: {priority}"

    messages = [
        {"role": "system", "content": TICKET_WRITER_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    summary = query[:300]
    suggested_action = "Review the issue and take appropriate action."

    try:
        raw = await _call_llm_with_retry(resolver_lm, messages)
        raw_text = raw[0] if isinstance(raw, list) else raw
        result = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
        summary = result.get("summary", summary)[:500]
        suggested_action = result.get("suggested_action", suggested_action)
    except Exception:
        pass

    return await _create_ticket(
        state, mcp_client, run_id, summary, suggested_action, team, priority, sla, customer_name
    )


# ── 6. Human Escalate ───────────────────────────────────────────────
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

    return await _create_ticket(
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
