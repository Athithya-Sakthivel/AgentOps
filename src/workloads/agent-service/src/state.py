# =============================================================================
# state.py - Minimal agent state
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.graph import MessagesState


class AgentState(MessagesState):
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
    """Only unsafe content bypasses the LLM. Everything else gets enriched."""
    if state.get("guardrail_rejected", False):
        return "human_escalate"
    # Everything goes through context gathering and the LLM ticket router
    return "context_gatherer"
