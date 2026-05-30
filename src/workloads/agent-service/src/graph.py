"""
LangGraph graph definition for the ticket triage agent.

Includes the new agentic nodes: action_dispatcher and response_formatter.
"""

from __future__ import annotations

import logging

from config import settings
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from nodes import (
    action_dispatcher,
    agentic_resolver,
    context_gatherer,
    guardrail_classifier,
    human_escalate,
    response_formatter,
)
from state import AgentState, Context, route_after_dispatcher, route_after_guardrail

log = logging.getLogger("agent-service")


def build_graph() -> StateGraph:
    builder = StateGraph(AgentState, context_schema=Context)

    # Add all nodes
    builder.add_node("guardrail_classifier", guardrail_classifier)
    builder.add_node("context_gatherer", context_gatherer)
    builder.add_node("action_dispatcher", action_dispatcher)  # NEW
    builder.add_node("agentic_resolver", agentic_resolver)
    builder.add_node("response_formatter", response_formatter)  # NEW
    builder.add_node("human_escalate", human_escalate)

    # Edge from START to guardrail
    builder.add_edge(START, "guardrail_classifier")

    # After guardrail: escalate or gather context
    builder.add_conditional_edges(
        "guardrail_classifier",
        route_after_guardrail,
        {
            "human_escalate": "human_escalate",
            "context_gatherer": "context_gatherer",
        },
    )

    # After context gatherer, always go to action_dispatcher
    builder.add_edge("context_gatherer", "action_dispatcher")

    # After action_dispatcher: if a deterministic action was taken, go to response_formatter,
    # otherwise fall back to the LLM agentic_resolver
    builder.add_conditional_edges(
        "action_dispatcher",
        route_after_dispatcher,
        {
            "response_formatter": "response_formatter",
            "agentic_resolver": "agentic_resolver",
        },
    )

    # After agentic_resolver, always go through response_formatter for final polish
    builder.add_edge("agentic_resolver", "response_formatter")

    # response_formatter is the final node for auto resolutions
    builder.add_edge("response_formatter", END)

    # human_escalate ends the graph as before
    builder.add_edge("human_escalate", END)

    return builder


async def compile_graph(checkpointer: AsyncPostgresSaver | None = None):
    builder = build_graph()

    if checkpointer is None:
        async with AsyncPostgresSaver.from_conn_string(settings.database_url) as cp:
            await cp.setup()
            graph = builder.compile(checkpointer=cp)
            log.info("Graph compiled with AsyncPostgresSaver")
            return graph

    graph = builder.compile(checkpointer=checkpointer)
    log.info("Graph compiled with provided checkpointer")
    return graph
