# =============================================================================
# graph.py – Deterministic dispatch + LLM ticket writer
# =============================================================================
from __future__ import annotations

import logging

from config import settings
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from nodes import (
    context_gatherer,
    guardrail_classifier,
    human_escalate,
    intent_router,
    policy_qa,
    ticket_writer,
)
from state import AgentState, Context, route_after_guardrail

log = logging.getLogger("agent-service")


def build_graph() -> StateGraph:
    builder = StateGraph(AgentState, context_schema=Context)

    builder.add_node("guardrail_classifier", guardrail_classifier)
    builder.add_node("context_gatherer", context_gatherer)
    builder.add_node("intent_router", intent_router)
    builder.add_node("policy_qa", policy_qa)
    builder.add_node("ticket_writer", ticket_writer)
    builder.add_node("human_escalate", human_escalate)

    builder.add_edge(START, "guardrail_classifier")

    builder.add_conditional_edges(
        "guardrail_classifier",
        route_after_guardrail,
        {
            "human_escalate": "human_escalate",
            "context_gatherer": "context_gatherer",
        },
    )

    # After gathering context, the intent router decides the path
    builder.add_edge("context_gatherer", "intent_router")

    # intent_router itself returns the final result (it calls policy_qa or ticket_writer internally)
    # so both are terminal nodes.
    builder.add_edge("intent_router", END)

    # These are used only by intent_router, not directly from the graph
    builder.add_node("policy_qa", policy_qa)
    builder.add_node("ticket_writer", ticket_writer)

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
