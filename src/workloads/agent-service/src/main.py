"""
Agent Service - entrypoint.

Exposes a WebSocket chat endpoint, admin REST routes, and OIDC authentication.
Uses LangGraph + DSPy + MCP tools. Structured logging only.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

import dspy
from auth.admin_middleware import AdminAuthMiddleware
from auth.auth_middleware import IdentityMiddleware
from compile_dspy import load_or_compile_triage
from config import create_resolver_lm, create_safeguard_lm, load_ssm_parameters, settings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from graph import compile_graph
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from logging_utils import log_event
from mcp_client import MCPClientManager
from policy_search import warmup_cache_async as warmup_policy_cache
from routes import router
from starlette.middleware.sessions import SessionMiddleware

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("agent-service")

for noisy in ("uvicorn.access", "uvicorn", "httpx", "httpcore", "openinference"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_event("INFO", "Starting Agent Service...")

    # Load secrets from SSM if not set via env
    load_ssm_parameters()

    resolver_lm = create_resolver_lm()
    app.state.resolver_lm = resolver_lm

    triage_program = load_or_compile_triage()
    app.state.triage_program = triage_program

    runtime_lm = create_safeguard_lm()
    dspy.configure(lm=runtime_lm)
    log_event("INFO", "Triage program loaded and DSPy configured")

    app.state.mcp_client = MCPClientManager()
    await app.state.mcp_client.connect()

    try:
        await warmup_policy_cache()
        log_event("INFO", "Policy search cache warmed")
    except Exception:
        log_event("WARN", "Policy search cache warmup failed - will retry on first request")

    async with AsyncPostgresSaver.from_conn_string(settings.database_url) as checkpointer:
        await checkpointer.setup()
        app.state.graph = await compile_graph(checkpointer=checkpointer)
        log_event("INFO", "LangGraph compiled")

        try:
            yield
        finally:
            log_event("INFO", "Shutting down...")
            await app.state.mcp_client.close()
            log_event("INFO", "Shutdown complete")


app = FastAPI(
    title="agent-service",
    version=settings.service_version,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

# Session middleware required for OIDC flow (stores state, nonce)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="agentops_session",
    same_site="lax",
    https_only=settings.deployment_environment != "local",
)

# CORS (adjust origins for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:3001").split(
        ","
    ),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

# Auth middlewares
app.add_middleware(IdentityMiddleware)  # attaches user claims from JWT
app.add_middleware(AdminAuthMiddleware)  # protects /admin/* by domain/tenant

app.include_router(router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    if hasattr(app.state, "mcp_client") and app.state.mcp_client._client:
        return {"status": "ready"}
    return {"status": "not_ready"}, 503


# Serve frontend static files (mounted at root)
frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    log_event("INFO", "Starting server", host=settings.host, port=settings.port)
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        log_level=LOG_LEVEL.lower(),
        log_config=None,
    )
