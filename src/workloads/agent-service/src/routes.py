"""
FastAPI routes for the Agent Service.
"""

from __future__ import annotations

import json
import logging
import uuid

from config import settings
from db import AsyncSessionLocal, HumanOverride, Ticket
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langchain_core.messages import HumanMessage
from logging_utils import log_event
from pydantic import BaseModel
from sqlalchemy import func, select
from state import Context

log = logging.getLogger("agent-service")
router = APIRouter()


class ChatRequest(BaseModel):
    query: str
    user_id: str | None = None


class OverrideRequest(BaseModel):
    ticket_id: str
    original_classification: dict
    corrected_classification: dict
    reason: str | None = None
    overridden_by: str | None = None


def _unwrap_ticket_id(ticket_id: object) -> str | None:
    """Extract a plain string ticket_id from MCP adapter wrapper types."""
    if ticket_id is None:
        return None
    if isinstance(ticket_id, str):
        return ticket_id
    if isinstance(ticket_id, list):
        for item in ticket_id:
            if isinstance(item, dict) and "text" in item:
                return str(item["text"])
            if isinstance(item, str):
                return item
    return str(ticket_id)


@router.websocket("/ws/chat/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str):
    await websocket.accept()

    app = websocket.app
    graph = app.state.graph
    triage_program = app.state.triage_program
    resolver_lm = app.state.resolver_lm
    mcp_client = app.state.mcp_client

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            query = data.get("query", "").strip()

            if not query:
                await websocket.send_json({"error": "query is required"})
                continue

            run_id = str(uuid.uuid4())
            log_event("INFO", "Message received", run_id=run_id, session_id=session_id)

            config = {"configurable": {"thread_id": session_id}}
            previous_state = None

            # Load previous conversation state if multi-turn is enabled
            if settings.multi_turn_enabled:
                try:
                    previous = await graph.aget_state(config)
                    if previous and previous.values:
                        prev_messages = previous.values.get("messages", [])
                        if len(prev_messages) >= settings.max_conversation_turns * 2:
                            log_event(
                                "INFO",
                                "Conversation turn limit reached, starting fresh",
                                run_id=run_id,
                            )
                        else:
                            previous_state = previous.values
                except Exception:
                    log_event(
                        "WARN",
                        "Failed to load previous state, starting fresh",
                        run_id=run_id,
                    )

            if previous_state:
                # Continue existing conversation
                state = dict(previous_state)
                state["messages"] = [
                    *state.get("messages", []),
                    HumanMessage(content=query),
                ]
                state["query_text"] = query
                state["run_id"] = run_id
                state["action_taken"] = False
                state["tool_results"] = []
                state["final_response"] = None
                state["error"] = None
                if data.get("user_id"):
                    state["user_id"] = data["user_id"]
            else:
                # Fresh conversation
                state = {
                    "messages": [HumanMessage(content=query)],
                    "query_text": query,
                    "user_id": data.get("user_id"),
                    "thread_id": session_id,
                    "run_id": run_id,
                    "guardrail_rejected": False,
                    "classification": None,
                    "customer_context": None,
                    "action_taken": False,
                    "tool_results": [],
                    "resolution_type": None,
                    "ticket_id": None,
                    "final_response": None,
                    "error": None,
                }

            ctx = Context(
                triage_program=triage_program,
                mcp_client=mcp_client,
                resolver_lm=resolver_lm,
            )

            result = await graph.ainvoke(state, config, context=ctx)

            clean_ticket_id = _unwrap_ticket_id(result.get("ticket_id"))

            log_event(
                "INFO",
                "Message processed",
                run_id=run_id,
                resolution_type=result.get("resolution_type"),
            )

            await websocket.send_json(
                {
                    "response": result.get("final_response", ""),
                    "resolution_type": result.get("resolution_type"),
                    "ticket_id": clean_ticket_id,
                }
            )

    except WebSocketDisconnect:
        log_event("INFO", "WebSocket disconnected", session_id=session_id)
    except Exception:
        log.exception("WebSocket error")
        await websocket.close()


@router.get("/admin/queue")
async def get_ticket_queue(limit: int = 50, offset: int = 0):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Ticket)
            .where(Ticket.status.in_(["open", "pending_human"]))
            .order_by(Ticket.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        tickets = result.scalars().all()
        return {
            "tickets": [
                {
                    "id": str(t.id),
                    "user_id": str(t.user_id),
                    "query_text": t.query_text,
                    "classification": t.classification,
                    "resolution_type": t.resolution_type,
                    "status": t.status,
                    "priority": t.priority,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in tickets
            ],
            "count": len(tickets),
        }


@router.post("/admin/override")
async def submit_override(body: OverrideRequest):
    async with AsyncSessionLocal() as session:
        override = HumanOverride(
            ticket_id=uuid.UUID(body.ticket_id),
            original_classification=body.original_classification,
            corrected_classification=body.corrected_classification,
            reason=body.reason,
            overridden_by=body.overridden_by,
        )
        session.add(override)
        await session.commit()
        return {"status": "stored", "id": str(override.id)}


@router.get("/admin/analytics")
async def get_analytics():
    async with AsyncSessionLocal() as session:
        total_result = await session.execute(select(func.count()).select_from(Ticket))
        total = total_result.scalar() or 0
        resolved_result = await session.execute(
            select(func.count())
            .select_from(Ticket)
            .where(Ticket.resolution_type == "auto_resolved")
        )
        auto_resolved = resolved_result.scalar() or 0
        override_result = await session.execute(select(func.count()).select_from(HumanOverride))
        overrides = override_result.scalar() or 0

    return {
        "total_tickets": total,
        "auto_resolved": auto_resolved,
        "auto_resolution_rate": round(auto_resolved / total * 100, 1) if total > 0 else 0,
        "human_overrides": overrides,
    }
