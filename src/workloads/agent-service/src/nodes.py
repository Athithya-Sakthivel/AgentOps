# =============================================================================
# nodes.py - Pragmatic agent: classify, gather context, route tickets
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
# Team routing (deterministic, based on DSPy intent)
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

    return {
        "guardrail_rejected": False,
        "classification": classification,
    }


# ---------------------------------------------------------------------------
# 2. Context Gatherer
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


# ---------------------------------------------------------------------------
# 3. Ticket Router (LLM with tools)
# ---------------------------------------------------------------------------
TICKET_ROUTER_SYSTEM_PROMPT = """You are Kestral's support triage specialist. Your job is to either answer the customer's question using our policy documents, or create a well-written ticket that captures the issue and routes it to the correct team.

You have access to:
- search_policies(query) - search internal policy documents
- create_ticket(user_id, query_text, classification, priority, assigned_team, summary, suggested_action) - create a support ticket

Rules:
1. Use the customer's name if you know it.
2. If the query is a simple policy question, answer it using search_policies. Do NOT create a ticket for policy questions.
3. Only create a ticket if the customer's issue requires human action (return, refund, complaint, replacement, investigation).
4. When creating a ticket, the summary MUST include:
   - The specific order ID and product name (from the "Recent orders" list)
   - What the customer expected vs. what they received (if applicable)
   - The exact policy rule that applies (if a policy was consulted)
5. The assigned_team and priority are provided for you - use them exactly as given. Do not change them.
6. Never promise a refund, credit, or pickup - just assure the customer that the right team will handle it.
7. If the query is ambiguous, ask a clarifying question instead of guessing.
8. When listing orders, use bullet points with format: "- Product Name (Order ID) - Status"

Respond in JSON:
- To call a tool: {"action": "tool_call", "tool": "<name>", "args": {<params>}}
- To reply: {"action": "final_answer", "response": "<message>"}
"""


async def ticket_router(state: AgentState, runtime: Runtime[Context]) -> dict[str, Any]:
    resolver_lm = runtime.context.resolver_lm
    mcp_client = runtime.context.mcp_client
    run_id = state.get("run_id", "")
    log_event("INFO", "Node started", node="ticket_router", run_id=run_id)

    query = state["query_text"]
    classification = state.get("classification", {})
    customer_ctx = state.get("customer_context") or {}
    customer = customer_ctx.get("customer") or {}
    orders = customer_ctx.get("orders") or []
    orders = [o for o in orders if isinstance(o, dict)]

    # Build messages
    messages = [
        {"role": "system", "content": TICKET_ROUTER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Customer: {customer.get('full_name', 'Unknown')} "
                f"({customer.get('segment', 'unknown')})\n"
                f"Customer ID: {state.get('user_id', 'unknown')}\n"
                f"Query: {query}\n"
                f"Classification: {_safe_json_dumps(classification)}\n"
                f"Recent orders: {_safe_json_dumps(orders[:3]) if orders else 'None'}\n"
                f"Assigned team: {_default_team(classification.get('intent', 'general_inquiry'))}\n"
                f"Priority: {'critical' if classification.get('urgency', 0) >= 9 else 'high' if classification.get('urgency', 0) >= 7 else 'medium'}"
            ),
        },
    ]

    for step in range(3):
        log_event("INFO", "Router step", run_id=run_id, step=step)
        try:
            raw = await resolver_lm.acall(messages=messages)
            raw_text = raw[0] if isinstance(raw, list) else raw
            result = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
        except (json.JSONDecodeError, KeyError):
            messages.append({"role": "user", "content": "Please respond with valid JSON."})
            continue

        action = result.get("action")
        if action == "final_answer":
            response = result.get("response", "I've noted your request.")
            log_event("INFO", "Router completed", run_id=run_id, steps=step + 1)
            return {
                "final_response": response,
                "resolution_type": "auto_resolved",
            }

        tool_name = result.get("tool")
        tool_args = result.get("args", {})

        if tool_name == "search_policies":
            try:
                policy_results = await asyncio.to_thread(
                    search_policies,
                    query=tool_args.get("query", query),
                    top_k=5,
                )
                tool_output = {"results": policy_results[:2]}  # truncate for token limit
            except Exception as exc:
                tool_output = {"error": str(exc)}
        elif tool_name == "create_ticket":
            # Merge in deterministic fields
            tool_args.setdefault("user_id", state.get("user_id"))
            tool_args.setdefault("query_text", query)
            tool_args.setdefault("classification", classification)
            tool_args.setdefault("priority", "medium")
            tool_args.setdefault(
                "assigned_team",
                _default_team(classification.get("intent", "general_inquiry")),
            )
            if "summary" not in tool_args:
                tool_args["summary"] = query[:300]
            if "suggested_action" not in tool_args:
                tool_args["suggested_action"] = "Review the issue and take appropriate action."
            try:
                raw_output = await mcp_client.call_tool(tool_name, tool_args, run_id=run_id)
                ticket_id = _safe_extract_data(raw_output)
                tool_output = {
                    "status": "created",
                    "ticket_id": str(ticket_id) if ticket_id else "unknown",
                }
            except Exception as exc:
                tool_output = {"error": str(exc)}
        else:
            tool_output = {"error": f"Unknown tool: {tool_name}"}

        messages.append({"role": "assistant", "content": json.dumps(result)})
        messages.append(
            {"role": "user", "content": f"Tool result: {_safe_json_dumps(tool_output)}"}
        )

    # Force final answer after max steps
    messages.append({"role": "user", "content": "Please give a final answer to the customer now."})
    raw = await resolver_lm.acall(messages=messages)
    raw_text = raw[0] if isinstance(raw, list) else raw
    try:
        final_result = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
        if isinstance(final_result, dict) and final_result.get("action") == "final_answer":
            final_response = final_result["response"]
        else:
            final_response = raw_text if isinstance(raw_text, str) else str(raw_text)
    except Exception:
        final_response = raw_text if isinstance(raw_text, str) else str(raw_text)

    return {
        "final_response": final_response,
        "resolution_type": "auto_resolved",
    }


# ---------------------------------------------------------------------------
# 4. Human Escalate (for unsafe / urgent cases)
# ---------------------------------------------------------------------------
async def human_escalate(state: AgentState, runtime: Runtime[Context]) -> dict[str, Any]:
    mcp_client = runtime.context.mcp_client
    run_id = state.get("run_id", "")
    log_event("INFO", "Node started", node="human_escalate", run_id=run_id)

    classification = state.get("classification", {})
    customer_ctx = state.get("customer_context") or {}
    customer = customer_ctx.get("customer") or {}
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
