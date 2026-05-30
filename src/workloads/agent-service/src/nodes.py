"""
LangGraph node implementations - production ready.

All nodes receive Runtime[Context] and use structured logging with run_id.

Includes:
  - guardrail_classifier   (DSPy triage)
  - context_gatherer       (customer + order lookup)
  - action_dispatcher      (deterministic tool-first dispatch)
  - agentic_resolver       (LLM-driven tool use)
  - response_formatter     (polished user-facing message)
  - human_escalate         (ticket creation + routing)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from config import settings
from langgraph.runtime import Runtime
from logging_utils import log_event
from policy_search import search_policies
from state import AgentState, Context

log = logging.getLogger("agent-service")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _prediction_to_dict(pred: Any) -> dict[str, Any]:
    if pred is None:
        return {}
    if isinstance(pred, dict):
        return pred
    result = {}
    for key, value in pred.items():
        if key.startswith("_"):
            continue
        result[key] = value
    return result


def _safe_json_dumps(obj: Any) -> str:
    if obj is None:
        return "null"
    if isinstance(obj, dict):
        return json.dumps({k: v for k, v in obj.items() if not k.startswith("_")})
    try:
        return json.dumps(obj)
    except (TypeError, ValueError):
        return json.dumps(str(obj))


def _summarise_for_llm(result: Any, max_chars: int = 1000) -> Any:
    if isinstance(result, dict):
        if "results" in result and isinstance(result["results"], list):
            truncated = []
            for r in result["results"][:2]:
                r_copy = dict(r)
                if "chunk_text" in r_copy:
                    r_copy["chunk_text"] = r_copy["chunk_text"][:300]
                truncated.append(r_copy)
            return {"results": truncated, "total": len(result["results"])}
        return {k: _summarise_for_llm(v, max_chars) for k, v in result.items()}
    if isinstance(result, str) and len(result) > max_chars:
        return result[:max_chars]
    return result


def _unwrap_nested_json(text: str) -> str:
    if not isinstance(text, str):
        return str(text)
    stripped = text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start and '"action"' in stripped:
        try:
            inner = json.loads(stripped[start : end + 1])
            if isinstance(inner, dict) and "response" in inner:
                return inner["response"]
        except (json.JSONDecodeError, KeyError):
            pass
    return text


def _resolve_order_id(orders: list[dict], query: str) -> str | None:
    if not orders:
        return None
    query_lower = query.lower()
    for o in orders:
        if not isinstance(o, dict):
            continue
        tracking = (o.get("tracking_number") or "").lower()
        if tracking and tracking in query_lower:
            return o["id"]
    for o in orders:
        if not isinstance(o, dict):
            continue
        product_name = (o.get("product_name") or "").lower()
        if product_name and any(word in query_lower for word in product_name.split()):
            return o["id"]
    for o in orders:
        if isinstance(o, dict):
            return o["id"]
    return None


def _extract_order_id_from_query(query: str) -> str | None:
    m = re.search(
        r"\b(?:ORD-\d+|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\b",
        query,
        re.IGNORECASE,
    )
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# deterministic action mapping (action_dispatcher)
# ---------------------------------------------------------------------------


def _next_business_day() -> str:
    today = datetime.now()
    if today.weekday() == 5:
        today = today + timedelta(days=2)
    elif today.weekday() == 6:
        today = today + timedelta(days=1)
    else:
        today = today + timedelta(days=1)
    return today.strftime("%Y-%m-%d")


INTENT_TO_ACTION = {
    "late_delivery": {
        "tool": "issue_wallet_credit",
        "args_template": {"amount": 100, "reason": "delivery delay compensation"},
        "max_amount": 500,
    },
    "delayed_delivery": {
        "tool": "issue_wallet_credit",
        "args_template": {"amount": 100, "reason": "delivery delay compensation"},
        "max_amount": 500,
    },
    "damaged_product": {
        "tool": "schedule_return_pickup",
        "args_template": {"pickup_date": _next_business_day},
    },
    "defective_product": {
        "tool": "schedule_return_pickup",
        "args_template": {"pickup_date": _next_business_day},
    },
    "return_request": {
        "tool": "check_refund_eligibility",
        "pre_check": True,
    },
    "refund_status": {
        "tool": "check_refund_eligibility",
        "pre_check": True,
    },
    "refund_query": {
        "tool": "check_refund_eligibility",
        "pre_check": True,
    },
    "refund_request": {
        "tool": "check_refund_eligibility",
        "pre_check": True,
    },
    "cancellation_request": {
        "tool": "check_refund_eligibility",
        "pre_check": True,
    },
    "wrong_item_delivered": {
        "tool": "check_refund_eligibility",
        "pre_check": True,
    },
}


# ---------------------------------------------------------------------------
# 1. GUARDRAIL + CLASSIFIER
# ---------------------------------------------------------------------------
async def guardrail_classifier(
    state: AgentState,
    runtime: Runtime[Context],
) -> dict[str, Any]:
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
            "final_response": (
                "Your message has been flagged for review. A human agent will respond shortly."
            ),
            "resolution_type": "escalated",
        }

    return {
        "guardrail_rejected": False,
        "classification": classification,
    }


# ---------------------------------------------------------------------------
# 2. CONTEXT GATHERER
# ---------------------------------------------------------------------------
async def context_gatherer(
    state: AgentState,
    runtime: Runtime[Context],
) -> dict[str, Any]:
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

    # Ensure orders is always a list
    if not isinstance(orders, list):
        orders = [orders] if orders else []

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


# ---------------------------------------------------------------------------
# 3. ACTION DISPATCHER  (deterministic tool-first)
# ---------------------------------------------------------------------------
async def action_dispatcher(
    state: AgentState,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    mcp_client = runtime.context.mcp_client
    run_id = state.get("run_id", "")
    classification = state.get("classification", {})
    customer_ctx = state.get("customer_context") or {}
    customer = customer_ctx.get("customer") or {}
    intent = classification.get("intent", "")

    log_event("INFO", "Node started", node="action_dispatcher", run_id=run_id, intent=intent)

    if not classification.get("auto_resolvable", False):
        log_event("INFO", "Not auto resolvable, deferring to LLM", run_id=run_id)
        return {"action_taken": False}

    mapping = INTENT_TO_ACTION.get(intent)
    if not mapping:
        log_event("INFO", "No deterministic action for intent", run_id=run_id, intent=intent)
        return {"action_taken": False}

    tool_name = mapping["tool"]

    # Build args, calling any callables in the template
    args = {}
    for k, v in mapping.get("args_template", {}).items():
        args[k] = v() if callable(v) else v

    # Only issue_wallet_credit needs user_id
    if tool_name == "issue_wallet_credit":
        user_id = state.get("user_id") or customer.get("id")
        if user_id:
            args["user_id"] = user_id
        amount = float(args.get("amount", 0))
        if amount > float(mapping.get("max_amount", settings.max_wallet_credit_amount)):
            args["amount"] = float(mapping["max_amount"])
        if "reason" not in args:
            args["reason"] = "goodwill gesture"
        args["amount"] = float(args["amount"])

    # Order-related tools need an order_id
    orders = customer_ctx.get("orders") or []
    orders = [o for o in orders if isinstance(o, dict)]

    if mapping.get("pre_check") or tool_name == "schedule_return_pickup":
        if "order_id" not in args:
            explicit_id = _extract_order_id_from_query(state.get("query_text", ""))
            if explicit_id:
                if any(
                    o.get("id") == explicit_id or o.get("tracking_number") == explicit_id
                    for o in orders
                ):
                    args["order_id"] = explicit_id
                else:
                    return {
                        "action_taken": True,
                        "tool_results": [
                            {
                                "tool": tool_name,
                                "args": {},
                                "result": {
                                    "error": f"Order {explicit_id} not found in your recent orders."
                                },
                            }
                        ],
                        "classification": classification,
                        "user_id": state.get("user_id"),
                        "customer_context": state.get("customer_context"),
                    }
            else:
                resolved = _resolve_order_id(orders, state.get("query_text", ""))
                if resolved:
                    args["order_id"] = resolved

        if "order_id" not in args:
            log_event(
                "WARN", "No order_id could be resolved, skipping", run_id=run_id, tool=tool_name
            )
            return {"action_taken": False}

    log_event("INFO", "Executing deterministic action", run_id=run_id, tool=tool_name)

    try:
        tool_output = await mcp_client.call_tool(tool_name, args, run_id=run_id)
        return {
            "action_taken": True,
            "tool_results": [{"tool": tool_name, "args": args, "result": tool_output}],
            "classification": classification,
            "user_id": state.get("user_id"),
            "customer_context": state.get("customer_context"),
        }
    except Exception as exc:
        log_event(
            "ERROR", "Deterministic action failed", run_id=run_id, tool=tool_name, error=str(exc)
        )
        return {"action_taken": False}


# ---------------------------------------------------------------------------
# 4. AGENTIC RESOLVER
# ---------------------------------------------------------------------------
RESOLVER_SYSTEM_PROMPT = """You are a helpful, empathetic customer service agent for Kestral, an Indian e-commerce company.

You MUST resolve issues by taking action, not just providing information.

Decision tree (follow in order):
1. If the customer reports a late delivery -> IMMEDIATELY call issue_wallet_credit
2. If the customer wants to return an item -> FIRST call check_refund_eligibility, THEN schedule_return_pickup if eligible
3. If the customer asks about refund status -> call check_refund_eligibility
4. If the customer reports a damaged / defective product -> call schedule_return_pickup
5. If the customer needs policy information -> call search_policies
6. ONLY if none of the above apply -> provide a text response

CRITICAL RULES FOR TOOL CALLS:
- When a tool requires an `order_id`, you MUST copy the exact `id` field from the
  "Recent orders" list. NEVER make up an ID.
- If no order in the list matches, respond: "I couldn't find that order in your
  recent purchases. Could you double-check the order number?"
- Do not guess amounts or dates. Use only values returned by the tools.

Rules:
- Never respond with text alone when a tool is available.
- Always confirm tool results to the customer with specific amounts, dates, and IDs.
- Use the customer's name when available.
- For high-value claims (>Rs.10,000) or security issues, create a ticket and escalate.
- Wallet credits cannot exceed Rs.500.

Respond in JSON:
- If you need to call a tool: {"action": "tool_call", "tool": "<name>", "args": {<params>}, "thought": "<why>"}
- If you have enough information to respond: {"action": "final_answer", "response": "<message to customer>"}
"""

MAX_RESOLVER_STEPS = 3


async def agentic_resolver(
    state: AgentState,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    resolver_lm = runtime.context.resolver_lm
    mcp_client = runtime.context.mcp_client
    run_id = state.get("run_id", "")

    log_event("INFO", "Node started", node="agentic_resolver", run_id=run_id)

    query = state["query_text"]
    classification = state.get("classification", {})
    customer_ctx = state.get("customer_context") or {}
    customer = customer_ctx.get("customer") or {}
    orders = customer_ctx.get("orders") or []
    orders = [o for o in orders if isinstance(o, dict)]

    messages = [
        {"role": "system", "content": RESOLVER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Customer: {customer.get('full_name', 'Unknown')} "
                f"({customer.get('segment', 'unknown')})\n"
                f"Query: {query}\n"
                f"Classification: {_safe_json_dumps(classification)}\n"
                f"Recent orders: {_safe_json_dumps(orders[:3]) if orders else 'None'}"
            ),
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
                "WARN", "Bad JSON from resolver, forcing final answer", run_id=run_id, step=step
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
            final_response = _unwrap_nested_json(final_response)
            log_event("INFO", "Forced final answer", run_id=run_id)
            return {
                "final_response": final_response,
                "resolution_type": "auto_resolved",
                "tool_results": tool_results,
            }

        action = result.get("action")
        log_event("INFO", "Resolver action", run_id=run_id, step=step, action=action)

        if action not in ("final_answer", "tool_call"):
            tool_name = action
            tool_args = result.get("args", {})
            action = "tool_call"
        else:
            tool_name = result.get("tool")
            tool_args = result.get("args", {})

        if action == "final_answer":
            final_text = result["response"]
            final_text = _unwrap_nested_json(final_text)
            log_event("INFO", "Resolution complete", run_id=run_id, steps=step + 1)
            return {
                "final_response": final_text,
                "resolution_type": "auto_resolved",
                "tool_results": tool_results,
            }

        # Validate order_id if required
        if tool_name in ("check_refund_eligibility", "schedule_return_pickup"):
            order_id = tool_args.get("order_id")
            if not order_id:
                tool_output = {
                    "error": "No order_id provided. Please specify which order you are referring to."
                }
                tool_results.append({"tool": tool_name, "args": tool_args, "result": tool_output})
                messages.append({"role": "assistant", "content": json.dumps(result)})
                messages.append(
                    {"role": "user", "content": f"Tool result: {_safe_json_dumps(tool_output)}"}
                )
                continue
            known_ids = {o.get("id") for o in orders if o.get("id")}
            if order_id not in known_ids:
                tool_output = {"error": f"Order ID {order_id} not found in your recent orders."}
                tool_results.append({"tool": tool_name, "args": tool_args, "result": tool_output})
                messages.append({"role": "assistant", "content": json.dumps(result)})
                messages.append(
                    {"role": "user", "content": f"Tool result: {_safe_json_dumps(tool_output)}"}
                )
                continue

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
                log_event("ERROR", "Policy search failed", run_id=run_id, error=str(exc))
        elif (
            tool_name == "issue_wallet_credit"
            and float(tool_args.get("amount", 0)) > settings.max_wallet_credit_amount
        ):
            tool_output = {
                "status": "rejected",
                "reason": f"Amount exceeds maximum of Rs.{settings.max_wallet_credit_amount}",
            }
        else:
            try:
                clean_args = {}
                for k, v in tool_args.items():
                    if k == "amount":
                        clean_args[k] = float(v)
                    elif isinstance(v, dict):
                        clean_args[k] = {
                            kk: vv for kk, vv in v.items() if not str(kk).startswith("_")
                        }
                    else:
                        clean_args[k] = v
                tool_output = await mcp_client.call_tool(tool_name, clean_args, run_id=run_id)
            except Exception as exc:
                tool_output = {"error": str(exc)}
                log_event(
                    "ERROR", "MCP tool call failed", run_id=run_id, tool=tool_name, error=str(exc)
                )

        tool_results.append({"tool": tool_name, "args": tool_args, "result": tool_output})
        messages.append({"role": "assistant", "content": json.dumps(result)})
        tool_summary = _summarise_for_llm(tool_output)
        messages.append(
            {"role": "user", "content": f"Tool result: {_safe_json_dumps(tool_summary)}"}
        )

    messages.append({"role": "user", "content": "Please give a final answer to the customer now."})
    raw = await resolver_lm.acall(messages=messages)
    raw_text = raw[0] if isinstance(raw, list) else raw
    final_response = raw_text if isinstance(raw_text, str) else str(raw_text)
    final_response = _unwrap_nested_json(final_response)
    log_event("INFO", "Max steps reached, forced final answer", run_id=run_id)
    return {
        "final_response": final_response,
        "resolution_type": "auto_resolved",
        "tool_results": tool_results,
    }


# ---------------------------------------------------------------------------
# 5. RESPONSE FORMATTER
# ---------------------------------------------------------------------------
async def response_formatter(
    state: AgentState,
    runtime: Runtime[Context],
) -> dict[str, Any]:
    run_id = state.get("run_id", "")
    log_event("INFO", "Node started", node="response_formatter", run_id=run_id)

    tool_results = state.get("tool_results") or []
    customer_ctx = state.get("customer_context") or {}
    customer = customer_ctx.get("customer") or {}
    customer_name = customer.get("full_name", "Hello")

    if tool_results:
        last = tool_results[-1]
        tool_name = last.get("tool", "")
        result = last.get("result", {})

        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                result = {"text": result}
        if not isinstance(result, dict):
            result = {"text": str(result)}

        if tool_name == "issue_wallet_credit":
            if result.get("status") == "issued":
                amount = result.get("amount", "unknown")
                txn_id = result.get("transaction_id", "")
                return {
                    "final_response": (
                        f"{customer_name}, I've issued Rs.{amount} as store credit "
                        f"(transaction #{txn_id}) for the inconvenience. "
                        "This will reflect in your wallet within 24 hours. Is there anything else I can help with?"
                    ),
                    "resolution_type": "auto_resolved",
                }
            else:
                return {
                    "final_response": (
                        f"{customer_name}, I tried to issue a credit but it was rejected: "
                        f"{result.get('reason', 'unknown reason')}. I'll escalate this for you."
                    ),
                    "resolution_type": "escalated",
                }

        if tool_name == "check_refund_eligibility":
            if result.get("eligible"):
                return {
                    "final_response": (
                        f"{customer_name}, your order is eligible for a refund of Rs.{result['amount']}. "
                        "I can schedule a return pickup for you. Would you like to proceed?"
                    ),
                    "resolution_type": "auto_resolved",
                }
            else:
                reason = result.get("reason", "not eligible")
                return {
                    "final_response": (
                        f"{customer_name}, I checked your refund eligibility: {reason}. "
                        "If you believe this is an error, I can escalate your case."
                    ),
                    "resolution_type": "auto_resolved",
                }

        if tool_name == "schedule_return_pickup":
            if result.get("status") == "scheduled":
                pickup = result.get("pickup_date", "soon")
                return {
                    "final_response": (
                        f"{customer_name}, a return pickup has been scheduled for {pickup}. "
                        "Once the item is received and inspected, your refund will be processed within 5-7 business days."
                    ),
                    "resolution_type": "auto_resolved",
                }
            else:
                reason = result.get("reason", "unknown")
                return {
                    "final_response": (
                        f"{customer_name}, I couldn't schedule the pickup: {reason}. Let me escalate this for you."
                    ),
                    "resolution_type": "escalated",
                }

        if result.get("error"):
            return {
                "final_response": (
                    f"{customer_name}, I ran into an issue: {result['error']}. "
                    "I'll escalate this to our team."
                ),
                "resolution_type": "escalated",
            }

        return {
            "final_response": f"{customer_name}, I've processed your request. Is there anything else I can help with?",
            "resolution_type": "auto_resolved",
        }

    final_response = (
        state.get("final_response") or "I wasn't able to process your request. Let me escalate it."
    )
    return {
        "final_response": final_response,
        "resolution_type": state.get("resolution_type", "auto_resolved"),
        "ticket_id": state.get("ticket_id"),
    }


# ---------------------------------------------------------------------------
# 6. HUMAN ESCALATE
# ---------------------------------------------------------------------------
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

    ticket_id = "unknown"
    try:
        ticket_id = await mcp_client.call_tool(
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
