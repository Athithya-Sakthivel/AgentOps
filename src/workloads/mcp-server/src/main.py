"""
MCP Server – entrypoint.

Exposes 3 tools for the pragmatic agent:
- lookup_customer
- get_recent_orders
- create_ticket

Runs DB migration on startup (idempotent).
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

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("mcp-server")

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

_pool: _db.asyncpg.Pool | None = None


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

    # Apply migrations (safe to run every startup)
    await _db.migrate_tickets_table(_pool)

    try:
        yield {"pool": _pool}
    finally:
        _log_tool("INFO", "Closing database pool", tool="lifespan")
        if _pool is not None:
            await _pool.close()
        _pool = None
        _log_tool("INFO", "Database pool closed", tool="lifespan")


mcp = FastMCP("mcp-server", lifespan=app_lifespan)


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
    description="Return the 5 most recent orders for a customer, including product details",
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
    name="create_ticket",
    description="Create a new support ticket with AI-generated summary and suggested action",
)
async def create_ticket_tool(
    user_id: str,
    query_text: str,
    classification: dict,
    priority: str,
    assigned_team: str = "general_support",
    summary: str | None = None,
    suggested_action: str | None = None,
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
            summary=summary,
            suggested_action=suggested_action,
            run_id=run_id,
            ctx=ctx,
        )
        _log_tool("INFO", "Tool call completed", tool="create_ticket", run_id=run_id)
        return result
    except Exception:
        _log_tool("ERROR", "Tool call failed", tool="create_ticket", run_id=run_id)
        raise


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


if __name__ == "__main__":
    _log_tool("INFO", "Starting mcp-server", tool="main", host=settings.host, port=settings.port)
    mcp.run(
        transport="http",
        host=settings.host,
        port=settings.port,
        log_level=LOG_LEVEL.lower(),
    )
