"""
MCP Server — entrypoint.

Exposes 9 tools. Structured JSON logging with run_id.
No OpenTelemetry, no vector database.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

import db as _db
import tools as _tools
from config import settings
from fastmcp import Context, FastMCP
from fastmcp.server.lifespan import lifespan
from starlette.requests import Request
from starlette.responses import PlainTextResponse

# ---------------------------------------------------------------------------
# Configure logging early so that all subsequent loggers respect the format.
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("mcp-server")

# Silence noisy third-party loggers
for noisy in (
    "uvicorn.access",
    "uvicorn",
    "httpx",
    "httpcore",
    "mcp.server.lowlevel.server",
    "mcp.server.sse",
    "sse_starlette.sse",
    "asyncio",
):
    logging.getLogger(noisy).setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_pool: _db.asyncpg.Pool | None = None


# ---------------------------------------------------------------------------
# Structured logging helper
# ---------------------------------------------------------------------------
def _log_tool(level: str, msg: str, tool: str, run_id: str = "", **kwargs: Any) -> None:
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": level,
        "message": msg,
        "tool": tool,
        "run_id": run_id,
        **kwargs,
    }
    print(json.dumps(payload, default=str), file=sys.stderr)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@lifespan
async def app_lifespan(server: FastMCP):
    global _pool
    _log_tool(
        "INFO",
        "Creating database pool",
        tool="lifespan",
        min=settings.pool_min_size,
        max=settings.pool_max_size,
    )
    _pool = await _db.create_pool()
    _log_tool("INFO", "Database pool ready", tool="lifespan")
    try:
        yield {"pool": _pool}
    finally:
        _log_tool("INFO", "Closing database pool", tool="lifespan")
        if _pool is not None:
            await _pool.close()
        _pool = None
        _log_tool("INFO", "Database pool closed", tool="lifespan")


# ---------------------------------------------------------------------------
# Create server
# ---------------------------------------------------------------------------
mcp = FastMCP("mcp-server", lifespan=app_lifespan)


# ---------------------------------------------------------------------------
# 9 tools - logging inline, no wrapper that destroys signatures
# ---------------------------------------------------------------------------


@mcp.tool(
    name="lookup_customer",
    description="Look up a customer by email or phone number",
)
async def lookup_customer_tool(
    email: str | None = None,
    phone: str | None = None,
    run_id: str = "",
    ctx: Context = None,
) -> dict | None:
    _log_tool("INFO", "Tool call started", tool="lookup_customer", run_id=run_id)
    try:
        result = await _tools.lookup_customer(email=email, phone=phone, run_id=run_id, ctx=ctx)
        _log_tool("INFO", "Tool call completed", tool="lookup_customer", run_id=run_id)
        return result
    except Exception:
        _log_tool("ERROR", "Tool call failed", tool="lookup_customer", run_id=run_id)
        raise


@mcp.tool(
    name="get_recent_orders",
    description="Return the 5 most recent orders for a customer",
)
async def get_recent_orders_tool(
    user_id: str,
    run_id: str = "",
    ctx: Context = None,
) -> list[dict]:
    _log_tool("INFO", "Tool call started", tool="get_recent_orders", run_id=run_id)
    try:
        result = await _tools.get_recent_orders(user_id=user_id, run_id=run_id, ctx=ctx)
        _log_tool("INFO", "Tool call completed", tool="get_recent_orders", run_id=run_id)
        return result
    except Exception:
        _log_tool("ERROR", "Tool call failed", tool="get_recent_orders", run_id=run_id)
        raise


@mcp.tool(
    name="get_order_details",
    description="Return full order details including product information",
)
async def get_order_details_tool(
    order_id: str,
    run_id: str = "",
    ctx: Context = None,
) -> dict | None:
    _log_tool("INFO", "Tool call started", tool="get_order_details", run_id=run_id)
    try:
        result = await _tools.get_order_details(order_id=order_id, run_id=run_id, ctx=ctx)
        _log_tool("INFO", "Tool call completed", tool="get_order_details", run_id=run_id)
        return result
    except Exception:
        _log_tool("ERROR", "Tool call failed", tool="get_order_details", run_id=run_id)
        raise


@mcp.tool(
    name="check_refund_eligibility",
    description="Check whether an order is eligible for refund",
)
async def check_refund_eligibility_tool(
    order_id: str,
    run_id: str = "",
    ctx: Context = None,
) -> dict:
    _log_tool("INFO", "Tool call started", tool="check_refund_eligibility", run_id=run_id)
    try:
        result = await _tools.check_refund_eligibility(order_id=order_id, run_id=run_id, ctx=ctx)
        _log_tool(
            "INFO",
            "Tool call completed",
            tool="check_refund_eligibility",
            run_id=run_id,
        )
        return result
    except Exception:
        _log_tool(
            "ERROR",
            "Tool call failed",
            tool="check_refund_eligibility",
            run_id=run_id,
        )
        raise


@mcp.tool(
    name="issue_wallet_credit",
    description="Issue wallet credit to a customer (max Rs.500)",
)
async def issue_wallet_credit_tool(
    user_id: str,
    amount: float,
    reason: str,
    run_id: str = "",
    ctx: Context = None,
) -> dict:
    _log_tool("INFO", "Tool call started", tool="issue_wallet_credit", run_id=run_id)
    try:
        result = await _tools.issue_wallet_credit(
            user_id=user_id, amount=amount, reason=reason, run_id=run_id, ctx=ctx
        )
        _log_tool("INFO", "Tool call completed", tool="issue_wallet_credit", run_id=run_id)
        return result
    except Exception:
        _log_tool("ERROR", "Tool call failed", tool="issue_wallet_credit", run_id=run_id)
        raise


@mcp.tool(
    name="schedule_return_pickup",
    description="Schedule a return pickup for an eligible order",
)
async def schedule_return_pickup_tool(
    order_id: str,
    pickup_date: str,
    run_id: str = "",
    ctx: Context = None,
) -> dict:
    _log_tool("INFO", "Tool call started", tool="schedule_return_pickup", run_id=run_id)
    try:
        result = await _tools.schedule_return_pickup(
            order_id=order_id,
            pickup_date=pickup_date,
            run_id=run_id,
            ctx=ctx,
        )
        _log_tool(
            "INFO",
            "Tool call completed",
            tool="schedule_return_pickup",
            run_id=run_id,
        )
        return result
    except Exception:
        _log_tool(
            "ERROR",
            "Tool call failed",
            tool="schedule_return_pickup",
            run_id=run_id,
        )
        raise


@mcp.tool(
    name="create_ticket",
    description="Create a new support ticket",
)
async def create_ticket_tool(
    user_id: str,
    query_text: str,
    classification: dict,
    priority: str,
    assigned_team: str = "general_support",
    run_id: str = "",
    ctx: Context = None,
) -> str:
    _log_tool("INFO", "Tool call started", tool="create_ticket", run_id=run_id)
    try:
        result = await _tools.create_ticket(
            user_id=user_id,
            query_text=query_text,
            classification=classification,
            priority=priority,
            assigned_team=assigned_team,
            run_id=run_id,
            ctx=ctx,
        )
        _log_tool("INFO", "Tool call completed", tool="create_ticket", run_id=run_id)
        return result
    except Exception:
        _log_tool("ERROR", "Tool call failed", tool="create_ticket", run_id=run_id)
        raise


@mcp.tool(
    name="escalate_to_human",
    description="Escalate a ticket to a human agent",
)
async def escalate_to_human_tool(
    ticket_id: str,
    run_id: str = "",
    ctx: Context = None,
) -> dict:
    _log_tool("INFO", "Tool call started", tool="escalate_to_human", run_id=run_id)
    try:
        result = await _tools.escalate_to_human(ticket_id=ticket_id, run_id=run_id, ctx=ctx)
        _log_tool("INFO", "Tool call completed", tool="escalate_to_human", run_id=run_id)
        return result
    except Exception:
        _log_tool("ERROR", "Tool call failed", tool="escalate_to_human", run_id=run_id)
        raise


@mcp.tool(
    name="route_to_team",
    description="Assign a ticket to a specific team queue",
)
async def route_to_team_tool(
    ticket_id: str,
    team: str,
    run_id: str = "",
    ctx: Context = None,
) -> dict:
    _log_tool("INFO", "Tool call started", tool="route_to_team", run_id=run_id)
    try:
        result = await _tools.route_to_team(ticket_id=ticket_id, team=team, run_id=run_id, ctx=ctx)
        _log_tool("INFO", "Tool call completed", tool="route_to_team", run_id=run_id)
        return result
    except Exception:
        _log_tool("ERROR", "Tool call failed", tool="route_to_team", run_id=run_id)
        raise


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


@mcp.custom_route("/readyz", methods=["GET"])
async def readyz(request: Request) -> PlainTextResponse:
    if _pool is None:
        return PlainTextResponse("not ready: pool not initialised", status_code=503)
    try:
        async with _pool.acquire() as conn:
            await conn.fetchrow("SELECT 1")
        return PlainTextResponse("ready")
    except Exception as exc:
        return PlainTextResponse(f"not ready: {exc}", status_code=503)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _log_tool(
        "INFO",
        "Starting mcp-server",
        tool="main",
        host=settings.host,
        port=settings.port,
    )
    mcp.run(
        transport="http",
        host=settings.host,
        port=settings.port,
        log_level=LOG_LEVEL.lower(),
    )
