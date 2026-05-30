"""
LangGraph node implementations - production ready.

All nodes receive Runtime[Context] and use structured logging with run_id.
No OpenTelemetry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from config import settings
from langgraph.runtime import Runtime
from logging_utils import log_event
from policy_search import search_policies
from state import AgentState, Context

log = logging.getLogger("agent-service")


# ======================================================================
# 1. GUARDRAIL + CLASSIFIER
# ======================================================================
async def guardrail_classifier(
    state: AgentState,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    """Safety check + ticket classification via DSPy TriageProgram."""

    triage_program = runtime.context.triage_program
    run_id = state.get("run_id", "")

    log_event("INFO", "Node started", node="guardrail_classifier", run_id=run_id)

    query = state["query_text"].strip()
    if not query or len(query) < 3:
        log_event("WARN", "Query too short, rejecting", run_id=run_id)
        return {
            "guardrail_rejected": True,
            "final_response": "I couldn't understand your message. Could you please rephrase?",
            "resolution_type": "escalated",
        }

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, triage_program, query)
    except Exception:
        log_event("ERROR", "Triage program failed", run_id=run_id)
        raise

    log_event(
        "INFO",
        "Triage completed",
        run_id=run_id,
        safety=result.get("safety", "UNKNOWN"),
        intent=result.get("intent", "unknown"),
    )

    if result.get("safety") == "UNSAFE":
        return {
            "guardrail_rejected": True,
            "classification": result,
            "final_response": (
                "Your message has been flagged for review. A human agent will respond shortly."
            ),
            "resolution_type": "escalated",
        }

    return {
        "guardrail_rejected": False,
        "classification": result,
    }


# ======================================================================
# 2. CONTEXT GATHERER
# ======================================================================
async def context_gatherer(
    state: AgentState,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    """Fetch customer profile and recent orders via MCP tools in parallel."""

    mcp_client = runtime.context.mcp_client
    run_id = state.get("run_id", "")

    log_event("INFO", "Node started", node="context_gatherer", run_id=run_id)

    query = state["query_text"]
    email_match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", query)
    email = email_match.group(0) if email_match else None
    user_id = state.get("user_id")

    if not email and not user_id:
        log_event("WARN", "No customer identifier - skipping context", run_id=run_id)
        return {"customer_context": None}

    customer = None
    if email:
        customer = await mcp_client.call_tool("lookup_customer", {"email": email}, run_id=run_id)
    if customer and customer.get("id"):
        user_id = customer["id"]

    orders = []
    if user_id:
        orders = await mcp_client.call_tool(
            "get_recent_orders", {"user_id": user_id}, run_id=run_id
        )

    log_event(
        "INFO",
        "Context gathered",
        run_id=run_id,
        customer_found=customer is not None,
        orders_count=len(orders),
    )

    return {
        "user_id": user_id,
        "customer_context": {"customer": customer, "orders": orders},
    }


# ======================================================================
# 3. AGENTIC RESOLVER
# ======================================================================
RESOLVER_SYSTEM_PROMPT = """You are a helpful, empathetic customer service agent for Kestral, an Indian e-commerce company.

You have access to these tools:
- search_policies(query) - search company policies (returns, refunds, delivery, warranty)
- check_refund_eligibility(order_id) - check if an order can be refunded
- issue_wallet_credit(user_id, amount, reason) - issue store credit (max Rs.500)
- schedule_return_pickup(order_id, pickup_date) - schedule a return pickup
- create_ticket(user_id, query_text, classification, priority, assigned_team) - create a support ticket
- escalate_to_human(ticket_id) - escalate a ticket to a human agent
- route_to_team(ticket_id, team) - assign a ticket to a specific team

Rules:
1. Gather information step by step. Never jump to conclusions.
2. Always ground your responses in retrieved policy documents.
3. Only use issue_wallet_credit for policy-driven compensation (delays, goodwill) and never more than Rs.500.
4. Only use schedule_return_pickup if the order is eligible for return.
5. For high-value claims (>Rs.10,000) or security issues, do NOT resolve automatically - create a ticket and escalate.
6. Use the customer's name when available. Include specific timelines and amounts from policies.
7. Output your reasoning, then the tool call or final answer.

Respond in JSON:
- If you need to call a tool: {"action": "tool_call", "tool": "<name>", "args": {<params>}, "thought": "<why>"}
- If you have enough information to respond: {"action": "final_answer", "response": "<message to customer>"}
"""

MAX_RESOLVER_STEPS = 5


async def agentic_resolver(
    state: AgentState,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    """Agentic loop that resolves tickets by calling tools dynamically."""

    resolver_lm = runtime.context.resolver_lm
    mcp_client = runtime.context.mcp_client
    run_id = state.get("run_id", "")

    log_event("INFO", "Node started", node="agentic_resolver", run_id=run_id)

    query = state["query_text"]
    classification = state.get("classification", {})
    customer_ctx = state.get("customer_context") or {}
    customer = customer_ctx.get("customer") or {}
    orders = customer_ctx.get("orders") or []

    messages = [
        {"role": "system", "content": RESOLVER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"""Customer: {customer.get("full_name", "Unknown")} ({customer.get("segment", "unknown")})
Query: {query}
Classification: {json.dumps(classification)}
Recent orders: {json.dumps(orders[:3]) if orders else "None"}""",
        },
    ]

    tool_results: list[dict] = []

    for step in range(MAX_RESOLVER_STEPS):
        log_event("INFO", "Resolver step", run_id=run_id, step=step)

        try:
            raw = await resolver_lm.acall(messages=messages)
            raw_text = raw[0] if isinstance(raw, list) else raw
            result = json.loads(raw_text) if isinstance(raw_text, str) else raw_text
        except (json.JSONDecodeError, KeyError):
            log_event(
                "WARN",
                "Bad JSON from resolver, forcing final answer",
                run_id=run_id,
                step=step,
            )
            messages.append(
                {
                    "role": "user",
                    "content": "Please give a final answer now. Do not call more tools.",
                }
            )
            raw = await resolver_lm.acall(messages=messages)
            raw_text = raw[0] if isinstance(raw, list) else raw
            final_response = raw_text if isinstance(raw_text, str) else str(raw_text)
            log_event("INFO", "Forced final answer", run_id=run_id)
            return {
                "final_response": final_response,
                "resolution_type": "auto_resolved",
                "tool_results": tool_results,
            }

        action = result.get("action")
        log_event("INFO", "Resolver action", run_id=run_id, step=step, action=action)

        if action == "final_answer":
            log_event("INFO", "Resolution complete", run_id=run_id, steps=step + 1)
            return {
                "final_response": result["response"],
                "resolution_type": "auto_resolved",
                "tool_results": tool_results,
            }

        # Tool call
        tool_name = result.get("tool")
        tool_args = result.get("args", {})

        if tool_name == "search_policies":
            try:
                policy_results = await asyncio.to_thread(
                    search_policies,
                    query=tool_args.get("query", query),
                    top_k=5,
                )
                tool_output = {"results": policy_results}
                log_event(
                    "INFO",
                    "Policy search executed",
                    run_id=run_id,
                    results_count=len(policy_results),
                )
            except Exception as exc:
                tool_output = {"error": str(exc)}
                log_event(
                    "ERROR",
                    "Policy search failed",
                    run_id=run_id,
                    error=str(exc),
                )
        elif (
            tool_name == "issue_wallet_credit"
            and tool_args.get("amount", 0) > settings.max_wallet_credit_amount
        ):
            tool_output = {
                "status": "rejected",
                "reason": (f"Amount exceeds maximum of Rs.{settings.max_wallet_credit_amount}"),
            }
        else:
            try:
                tool_output = await mcp_client.call_tool(tool_name, tool_args, run_id=run_id)
            except Exception as exc:
                tool_output = {"error": str(exc)}
                log_event(
                    "ERROR",
                    "MCP tool call failed",
                    run_id=run_id,
                    tool=tool_name,
                    error=str(exc),
                )

        tool_results.append({"tool": tool_name, "args": tool_args, "result": tool_output})
        messages.append({"role": "assistant", "content": json.dumps(result)})
        messages.append({"role": "user", "content": f"Tool result: {json.dumps(tool_output)}"})

    # Max steps reached - force final answer
    messages.append({"role": "user", "content": "Please give a final answer to the customer now."})
    raw = await resolver_lm.acall(messages=messages)
    raw_text = raw[0] if isinstance(raw, list) else raw
    final_response = raw_text if isinstance(raw_text, str) else str(raw_text)
    log_event("INFO", "Max steps reached, forced final answer", run_id=run_id)
    return {
        "final_response": final_response,
        "resolution_type": "auto_resolved",
        "tool_results": tool_results,
    }


# ======================================================================
# 4. HUMAN ESCALATE
# ======================================================================
TEAM_ROUTING = {
    "wrong_item_delivered": "order_fulfillment",
    "damaged_product": "service_center",
    "late_delivery": "logistics",
    "refund_status": "payments",
    "cancellation_request": "order_fulfillment",
    "return_request": "order_fulfillment",
    "warranty_claim": "service_center",
    "payment_issue": "payments",
    "account_issue": "senior_support",
    "general_inquiry": "general_support",
    "complaint": "senior_support",
}


async def human_escalate(
    state: AgentState,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    """Create a ticket, escalate, and route to the correct team."""

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
    team = TEAM_ROUTING.get(intent, "general_support")

    try:
        ticket_id = await mcp_client.call_tool(
            "create_ticket",
            {
                "user_id": state.get("user_id", "unknown"),
                "query_text": state["query_text"],
                "classification": classification,
                "priority": priority,
                "assigned_team": team,
            },
            run_id=run_id,
        )
        await mcp_client.call_tool("escalate_to_human", {"ticket_id": ticket_id}, run_id=run_id)
        await mcp_client.call_tool(
            "route_to_team", {"ticket_id": ticket_id, "team": team}, run_id=run_id
        )
        log_event(
            "INFO",
            "Ticket created and escalated",
            run_id=run_id,
            ticket_id=ticket_id,
            priority=priority,
            team=team,
        )
    except Exception:
        log_event("ERROR", "Failed to create/escalate ticket", run_id=run_id)
        ticket_id = "unknown"

    response = (
        f"{customer.get('full_name', 'Hello')}, your issue has been flagged as "
        f"{priority} priority. A {team.replace('_', ' ')} specialist will review "
        f"your case within {sla}. Your reference number is {ticket_id}."
    )
    return {
        "ticket_id": ticket_id,
        "final_response": response,
        "resolution_type": "escalated",
    }
