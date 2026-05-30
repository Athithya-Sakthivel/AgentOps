"""
AgentState TypedDict, Context dataclass, and routing conditions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.graph import MessagesState


class AgentState(MessagesState):
    """Full agent state flowing through every node."""

    user_id: str | None
    query_text: str
    thread_id: str
    run_id: str  # correlation ID

    guardrail_rejected: bool
    classification: dict[str, Any] | None

    customer_context: dict[str, Any] | None

    action_taken: bool  # set by action_dispatcher when a deterministic tool call succeeds

    tool_results: list[dict[str, Any]]
    resolution_type: str | None
    ticket_id: str | None

    final_response: str | None
    error: str | None


@dataclass
class Context:
    """Runtime dependencies injected via graph.ainvoke(..., context=Context(...))."""

    triage_program: Any
    mcp_client: Any
    resolver_lm: Any


def route_after_guardrail(state: AgentState) -> str:
    if state.get("guardrail_rejected", False):
        return "human_escalate"

    classification = state.get("classification", {})
    auto_resolvable = classification.get("auto_resolvable", True)
    urgency = classification.get("urgency", 0)

    # Only escalate if NOT auto-resolvable OR urgency is critical (10)
    if not auto_resolvable or urgency >= 10:
        return "human_escalate"

    return "context_gatherer"


def route_after_dispatcher(state: AgentState) -> str:
    """
    After action_dispatcher, either go to response_formatter (if a direct
    action was taken) or fall back to the LLM-driven agentic_resolver.
    """
    if state.get("action_taken", False):
        return "response_formatter"
    return "agentic_resolver"
