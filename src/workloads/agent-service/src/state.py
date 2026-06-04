# =============================================================================
# state.py – Only guardrail rejection escalates; everything else → conversational
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

    # Store the last assistant response for deterministic confirmations
    last_assistant_message: str | None


@dataclass
class Context:
    triage_program: Any
    mcp_client: Any
    resolver_lm: Any


def route_after_guardrail(state: AgentState) -> str:
    """Only truly unsafe content bypasses the conversational agent."""
    if state.get("guardrail_rejected", False):
        return "human_escalate"
    return "context_gatherer"
