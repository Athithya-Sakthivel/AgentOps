# =============================================================================
# state.py - Minimal agent state (classifier + router)
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.graph import MessagesState


class AgentState(MessagesState):
    """State that flows through guardrail -> context -> ticket router."""

    user_id: str | None
    user_email: str | None
    query_text: str
    thread_id: str
    run_id: str

    guardrail_rejected: bool
    classification: dict[str, Any] | None

    customer_context: dict[str, Any] | None

    resolution_type: str | None
    ticket_id: str | None
    final_response: str | None
    error: str | None


@dataclass
class Context:
    triage_program: Any
    mcp_client: Any
    resolver_lm: Any


def route_after_guardrail(state: AgentState) -> str:
    if state.get("guardrail_rejected", False):
        return "human_escalate"
    classification = state.get("classification", {})
    if not classification.get("auto_resolvable", True) or classification.get("urgency", 0) >= 10:
        return "human_escalate"
    return "context_gatherer"
