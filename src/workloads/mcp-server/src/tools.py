"""
MCP tool implementations – only the tools used by the pragmatic agent.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

import db
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


async def create_ticket(
    user_id: str,
    query_text: str,
    classification: dict,
    priority: str,
    assigned_team: str = "general_support",
    summary: str | None = None,
    suggested_action: str | None = None,
    *,
    run_id: str = "",
    ctx: Context,
) -> str:
    pool = ctx.lifespan_context["pool"]
    ticket_id = await db.insert_ticket(
        pool,
        user_id,
        query_text,
        classification,
        priority,
        assigned_team,
        summary,
        suggested_action,
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
