# =============================================================================
# nodes.py - Final pragmatic agent (all tests green, lint clean)
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Team routing (deterministic)
# ---------------------------------------------------------------------------
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


def _is_actionable_intent(intent: str) -> bool:
    return intent in INTENT_TO_TEAM and intent != "general_inquiry"


# ---------------------------------------------------------------------------
# 1. Guardrail + Classifier
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 2. Context Gatherer – customer always a dict
# ---------------------------------------------------------------------------
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

    # Look up customer, but keep a guaranteed‑dict variable
    looked_up: dict[str, Any] | None = None
    if email:
        raw = await mcp_client.call_tool("lookup_customer", {"email": email}, run_id=run_id)
        raw = _safe_extract_data(raw)
        if isinstance(raw, dict):
            looked_up = raw
        elif isinstance(raw, list) and raw:
            first = raw[0]
            if isinstance(first, dict):
                looked_up = first

    # customer is ALWAYS a dict from here on
    customer: dict[str, Any] = looked_up if looked_up is not None else {}

    if customer.get("id"):
        user_id = customer["id"]

    if user_id and isinstance(user_id, str) and user_id.startswith("google#"):
        log_event(
            "WARN",
            "Customer not found in DB, cannot fetch orders",
            run_id=run_id,
        )
        return {
            "user_id": user_id,
            "user_email": email,
            "customer_context": None,
        }

    orders: list[dict[str, Any]] = []
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
        customer_found=bool(customer),
        orders_count=len(orders),
    )
    return {
        "user_id": user_id,
        "user_email": email,
        "customer_context": {"customer": customer if customer else None, "orders": orders},
    }


# ---------------------------------------------------------------------------
# 3. Ticket Router
# ---------------------------------------------------------------------------
TICKET_ROUTER_SYSTEM_PROMPT = """You are Kestral's support triage specialist. Your job is to either answer the customer's question using our policy documents, or create a well-written ticket that captures the issue and routes it to the correct team.

You have access to:
- search_policies(query) - search internal policy documents
- create_ticket(user_id, query_text, classification, priority, assigned_team, summary, suggested_action) - create a support ticket

Rules:
1. Use the customer's name if you know it.
2. If the query is a simple policy question, answer it using search_policies. Do NOT create a ticket for policy questions.
3. Only create a ticket if the customer's issue requires human action (return, refund, complaint, replacement, investigation).
4. When you decide to create a ticket, include a `summary` (2-3 sentences) and a `suggested_action` (one sentence) in your tool call.
5. The assigned_team and priority are provided for you - use them exactly as given. Do not change them.
6. Never promise a refund, credit, or pickup - just assure the customer that the right team will handle it.
7. If the query is ambiguous, ask a clarifying question instead of guessing.

Respond in JSON:
- To call a tool: {"action": "tool_call", "tool": "<name>", "args": {<params>}}
- To reply: {"action": "final_answer", "response": "<message>"}
"""


async def _call_llm_with_retry(lm, messages, max_retries=3):
    for attempt in range(max_retries):
        try:
            return await lm.acall(messages=messages)
        except Exception as e:
            if "429" in str(e) or "Too many requests" in str(e):
                if attempt == max_retries - 1:
                    raise
                wait = 2**attempt
                log.warning("Bedrock rate limit hit, retrying in %ds...", wait)
                await asyncio.sleep(wait)
            else:
                raise


async def ticket_router(state: AgentState, runtime: Runtime[Context]) -> dict[str, Any]:
    resolver_lm = runtime.context.resolver_lm
    mcp_client = runtime.context.mcp_client
    run_id = state.get("run_id", "")
    log_event("INFO", "Node started", node="ticket_router", run_id=run_id)

    query = state["query_text"]

    # --- Explicit None checks for classification ---
    raw_classification = state.get("classification")
    classification: dict[str, Any] = (
        raw_classification if isinstance(raw_classification, dict) else {}
    )

    # --- Explicit None checks for customer context ---
    raw_customer_ctx = state.get("customer_context")
    customer_ctx: dict[str, Any] = raw_customer_ctx if isinstance(raw_customer_ctx, dict) else {}

    # Ensure customer is always a dict
    raw_customer = customer_ctx.get("customer")
    customer: dict[str, Any] = raw_customer if isinstance(raw_customer, dict) else {}

    orders: list[dict[str, Any]] = [
        o for o in (customer_ctx.get("orders") or []) if isinstance(o, dict)
    ]

    # ── Fake claim detection ──────────────────────────────────────
    mentioned_order_id = None
    m = re.search(r"\bORD-\d+\b", query, re.IGNORECASE)
    if m:
        mentioned_order_id = m.group(0)
    else:
        m = re.search(
            r"\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b",
            query,
            re.IGNORECASE,
        )
        if m:
            mentioned_order_id = m.group(0)

    if mentioned_order_id:
        known_ids = {o.get("id") for o in orders} | {o.get("tracking_number") for o in orders}
        if mentioned_order_id not in known_ids:
            customer_name = customer.get("full_name", "Hello")
            return {
                "final_response": (
                    f"{customer_name}, I couldn't find order {mentioned_order_id} "
                    "in your recent purchases. Could you double‑check the order number?"
                ),
                "resolution_type": "auto_resolved",
            }

    intent = classification.get("intent", "general_inquiry")
    team = _default_team(intent)
    urgency = classification.get("urgency", 0)
    priority = "critical" if urgency >= 9 else "high" if urgency >= 7 else "medium"
    sla = "2 hours" if urgency >= 9 else "4 hours" if urgency >= 7 else "24 hours"
    customer_name = customer.get("full_name", "Hello")

    # ── Deterministic ticket creation ─────────────────────────────
    if _is_actionable_intent(intent):
        summary = f"Customer reported: {query}. " + (
            f"Order details: {_safe_json_dumps(orders[:2])}" if orders else "No recent orders."
        )
        suggested_action = "Review the issue and take appropriate action."
        try:
            raw = await mcp_client.call_tool(
                "create_ticket",
                {
                    "user_id": state.get("user_id"),
                    "query_text": query,
                    "classification": classification,
                    "priority": priority,
                    "assigned_team": team,
                    "summary": summary[:500],
                    "suggested_action": suggested_action,
                },
                run_id=run_id,
            )
            ticket_id = _safe_extract_data(raw)
            ticket_id_str = str(ticket_id) if ticket_id else "unknown"
            response = (
                f'{customer_name}, I\'ve created a ticket regarding: "{query}". '
                f"The {team.replace('_', ' ')} team will review it within {sla}. "
                f"Your reference number is {ticket_id_str}."
            )
            log_event(
                "INFO",
                "Ticket created deterministically",
                run_id=run_id,
                ticket_id=ticket_id_str,
                team=team,
            )
            return {
                "final_response": response,
                "resolution_type": "auto_resolved",
                "ticket_id": ticket_id_str,
            }
        except Exception as exc:
            log_event("ERROR", "Failed to create ticket", run_id=run_id, error=str(exc))
            return {
                "final_response": "I'm sorry, I'm having trouble creating your ticket. A human agent will assist you shortly.",
                "resolution_type": "escalated",
            }

    # ── Policy Q&A via LLM ────────────────────────────────────────
    messages = [
        {"role": "system", "content": TICKET_ROUTER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Customer: {customer_name} ({customer.get('segment', 'unknown')})\n"
                f"Customer ID: {state.get('user_id', 'unknown')}\n"
                f"Query: {query}\n"
                f"Classification: {_safe_json_dumps(classification)}\n"
                f"Recent orders: {_safe_json_dumps(orders[:3]) if orders else 'None'}\n"
                f"Assigned team: {team}\n"
                f"Priority: {priority}"
            ),
        },
    ]

    for step in range(3):
        log_event("INFO", "Router step", run_id=run_id, step=step)
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
            log_event("INFO", "Router completed", run_id=run_id, steps=step + 1)
            return {"final_response": response, "resolution_type": "auto_resolved"}

        tool_name = result.get("tool")
        tool_args = result.get("args", {})

        if tool_name == "search_policies":
            try:
                policy_results = await asyncio.to_thread(
                    search_policies, query=tool_args.get("query", query), top_k=5
                )
                tool_output = {"results": policy_results[:2]}
            except Exception as exc:
                tool_output = {"error": str(exc)}

        elif tool_name == "create_ticket":
            tool_args.setdefault("user_id", state.get("user_id"))
            tool_args.setdefault("query_text", query)
            tool_args.setdefault("classification", classification)
            tool_args.setdefault("priority", priority)
            tool_args.setdefault("assigned_team", team)
            tool_args.setdefault("summary", query[:300])
            tool_args.setdefault(
                "suggested_action", "Review the issue and take appropriate action."
            )
            try:
                raw_output = await mcp_client.call_tool(tool_name, tool_args, run_id=run_id)
                ticket_id = _safe_extract_data(raw_output)
                ticket_id_str = str(ticket_id) if ticket_id else "unknown"
                response = (
                    f'{customer_name}, I\'ve created a ticket regarding: "{query}". '
                    f"The {team.replace('_', ' ')} team will review it within {sla}. "
                    f"Your reference number is {ticket_id_str}."
                )
                return {
                    "final_response": response,
                    "resolution_type": "auto_resolved",
                    "ticket_id": ticket_id_str,
                }
            except Exception as exc:
                tool_output = {"error": str(exc)}
        else:
            tool_output = {"error": f"Unknown tool: {tool_name}"}

        messages.append({"role": "assistant", "content": json.dumps(result)})
        messages.append(
            {"role": "user", "content": f"Tool result: {_safe_json_dumps(tool_output)}"}
        )

    return {
        "final_response": "I'm sorry, I'm having trouble processing your request. A human agent will assist you shortly.",
        "resolution_type": "escalated",
    }


# ---------------------------------------------------------------------------
# 4. Human Escalate
# ---------------------------------------------------------------------------
async def human_escalate(state: AgentState, runtime: Runtime[Context]) -> dict[str, Any]:
    mcp_client = runtime.context.mcp_client
    run_id = state.get("run_id", "")
    log_event("INFO", "Node started", node="human_escalate", run_id=run_id)

    # Explicit None checks
    raw_classification = state.get("classification")
    classification: dict[str, Any] = (
        raw_classification if isinstance(raw_classification, dict) else {}
    )

    raw_customer_ctx = state.get("customer_context")
    customer_ctx: dict[str, Any] = raw_customer_ctx if isinstance(raw_customer_ctx, dict) else {}

    raw_customer = customer_ctx.get("customer")
    customer: dict[str, Any] = raw_customer if isinstance(raw_customer, dict) else {}

    urgency = classification.get("urgency", 5)
    intent = classification.get("intent", "general_inquiry")
    priority = "critical" if urgency >= 9 else "high" if urgency >= 7 else "medium"
    sla = "2 hours" if urgency >= 9 else "4 hours" if urgency >= 7 else "24 hours"
    team = _default_team(intent)

    ticket_id = "unknown"
    try:
        raw = await mcp_client.call_tool(
            "create_ticket",
            {
                "user_id": state.get("user_id", "unknown"),
                "query_text": state["query_text"],
                "classification": _prediction_to_dict(classification),
                "priority": priority,
                "assigned_team": team,
            },
            run_id=run_id,
        )
        ticket_id = _safe_extract_data(raw)
        if not isinstance(ticket_id, str):
            ticket_id = str(ticket_id) if ticket_id else "unknown"
        log_event(
            "INFO",
            "Ticket created",
            run_id=run_id,
            ticket_id=ticket_id,
            priority=priority,
            team=team,
        )
    except Exception:
        log_event("ERROR", "Failed to create ticket", run_id=run_id)

    customer_name = customer.get("full_name", "Hello")
    response = (
        f"{customer_name}, your issue has been flagged as {priority} priority. "
        f"A {team.replace('_', ' ')} specialist will review your case within {sla}. "
        f"Your reference number is {ticket_id}."
    )
    return {
        "ticket_id": ticket_id,
        "final_response": response,
        "resolution_type": "escalated",
    }
