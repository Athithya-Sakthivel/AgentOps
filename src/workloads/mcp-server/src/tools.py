"""
MCP tool implementations — 9 tools.

All PostgreSQL operations. search_policies moved to agent-service.
Structured logging with run_id.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import db
from config import settings
from fastmcp import Context


def _log(level: str, msg: str, run_id: str = "", **kwargs: Any) -> None:
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": level,
        "message": msg,
        "run_id": run_id,
        **kwargs,
    }
    print(json.dumps(payload, default=str), file=sys.stderr)


# ── Read Tools ────────────────────────────────────────────────


async def lookup_customer(
    email: str | None = None,
    phone: str | None = None,
    *,
    run_id: str = "",
    ctx: Context,
) -> dict[str, Any] | None:
    pool = ctx.lifespan_context["pool"]
    if email:
        result = await db.get_user_by_email(pool, email)
    elif phone:
        result = await db.get_user_by_phone(pool, phone)
    else:
        result = None
    _log("DEBUG", "lookup_customer result", run_id=run_id, found=result is not None)
    return result


async def get_recent_orders(
    user_id: str,
    *,
    run_id: str = "",
    ctx: Context,
) -> list[dict[str, Any]]:
    pool = ctx.lifespan_context["pool"]
    result = await db.get_recent_orders(pool, user_id)
    _log("DEBUG", "get_recent_orders", run_id=run_id, count=len(result))
    return result


async def get_order_details(
    order_id: str,
    *,
    run_id: str = "",
    ctx: Context,
) -> dict[str, Any] | None:
    pool = ctx.lifespan_context["pool"]
    result = await db.get_order_with_product(pool, order_id)
    _log("DEBUG", "get_order_details", run_id=run_id, found=result is not None)
    return result


async def check_refund_eligibility(
    order_id: str,
    *,
    run_id: str = "",
    ctx: Context,
) -> dict[str, Any]:
    pool = ctx.lifespan_context["pool"]
    order = await db.get_order_with_product(pool, order_id)
    if not order:
        return {"eligible": False, "reason": "order_not_found"}

    billing_rows = await db.get_billing_by_order(pool, order_id)
    for br in billing_rows:
        if br["transaction_type"] == "refund" and br["status"] == "completed":
            return {
                "eligible": False,
                "reason": "already_refunded",
                "refund_id": br["gateway_transaction_id"],
            }

    delivery_date = order.get("delivery_date")
    return_window = order.get("return_window_days", 10)
    if delivery_date and return_window:
        days_since = (datetime.now(UTC) - delivery_date).days
        if days_since > return_window:
            return {"eligible": False, "reason": f"return_window_expired ({return_window} days)"}

    payment = next((br for br in billing_rows if br["transaction_type"] == "payment"), None)
    if not payment:
        return {"eligible": False, "reason": "no_payment_found"}

    result = {
        "eligible": True,
        "reason": "within_return_window",
        "amount": payment["amount"],
        "method": order.get("payment_method", "upi"),
    }
    _log("DEBUG", "check_refund_eligibility", run_id=run_id, eligible=True)
    return result


# ── Action Tools ──────────────────────────────────────────────


async def issue_wallet_credit(
    user_id: str,
    amount: float,
    reason: str,
    *,
    run_id: str = "",
    ctx: Context,
) -> dict[str, Any]:
    if amount > float(settings.max_wallet_credit_amount):
        return {
            "status": "rejected",
            "reason": f"Amount exceeds maximum of Rs.{settings.max_wallet_credit_amount}",
        }

    pool = ctx.lifespan_context["pool"]
    ref_id = await db.issue_wallet_credit(pool, user_id, Decimal(str(amount)), reason)
    _log("INFO", "Wallet credit issued", run_id=run_id, transaction_id=ref_id, amount=amount)
    return {"status": "issued", "transaction_id": ref_id, "amount": amount}


async def schedule_return_pickup(
    order_id: str,
    pickup_date: str,
    *,
    run_id: str = "",
    ctx: Context,
) -> dict[str, Any]:
    pool = ctx.lifespan_context["pool"]
    eligibility = await check_refund_eligibility(order_id=order_id, run_id=run_id, ctx=ctx)
    if not eligibility.get("eligible"):
        return {"status": "failed", "reason": eligibility.get("reason", "not_eligible")}

    await db.schedule_return_pickup(pool, order_id, pickup_date)
    _log(
        "INFO", "Return pickup scheduled", run_id=run_id, order_id=order_id, pickup_date=pickup_date
    )
    return {"status": "scheduled", "order_id": order_id, "pickup_date": pickup_date}


# ── Escalation Tools ─────────────────────────────────────────


async def create_ticket(
    user_id: str,
    query_text: str,
    classification: dict,
    priority: str,
    assigned_team: str = "general_support",
    *,
    run_id: str = "",
    ctx: Context,
) -> str:
    pool = ctx.lifespan_context["pool"]
    ticket_id = await db.insert_ticket(
        pool, user_id, query_text, classification, priority, assigned_team
    )
    _log(
        "INFO",
        "Ticket created",
        run_id=run_id,
        ticket_id=ticket_id,
        priority=priority,
        team=assigned_team,
    )
    return ticket_id


async def escalate_to_human(
    ticket_id: str,
    *,
    run_id: str = "",
    ctx: Context,
) -> dict[str, Any]:
    pool = ctx.lifespan_context["pool"]
    await db.update_ticket_status(pool, ticket_id, "pending_human")
    await db.set_ticket_priority(pool, ticket_id, "critical")
    _log("INFO", "Ticket escalated", run_id=run_id, ticket_id=ticket_id)
    return {"status": "escalated", "ticket_id": ticket_id}


async def route_to_team(
    ticket_id: str,
    team: str,
    *,
    run_id: str = "",
    ctx: Context,
) -> dict[str, Any]:
    pool = ctx.lifespan_context["pool"]
    await db.assign_ticket_team(pool, ticket_id, team)
    _log("INFO", "Ticket routed", run_id=run_id, ticket_id=ticket_id, team=team)
    return {"status": "routed", "ticket_id": ticket_id, "team": team}
