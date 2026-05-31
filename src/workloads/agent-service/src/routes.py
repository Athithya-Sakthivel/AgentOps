"""
FastAPI routes for the Agent Service.

Chat, admin, and OIDC authentication endpoints.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import httpx
from auth.dynamodb_rate_limiter import check_rate_limit
from auth.jwt_utils import verify_access_token
from authlib.integrations.starlette_client import OAuth, OAuthError
from config import settings
from db import AsyncSessionLocal, HumanOverride, Ticket
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import ECKey
from langchain_core.messages import HumanMessage
from logging_utils import log_event
from pydantic import BaseModel
from sqlalchemy import func, select
from state import Context

log = logging.getLogger("agent-service")
router = APIRouter()


# ── Models ──────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    query: str
    user_id: str | None = None


class OverrideRequest(BaseModel):
    ticket_id: str
    original_classification: dict
    corrected_classification: dict
    reason: str | None = None
    overridden_by: str | None = None


# ── Helpers ─────────────────────────────────────────────────────────
def _unwrap_ticket_id(ticket_id: object) -> str | None:
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


# ── OIDC Auth Setup ─────────────────────────────────────────────────
oauth = OAuth()
_GOOGLE_SVG = '<svg viewBox="0 0 24 24" width="18" height="18" xmlns="http://www.w3.org/2000/svg"><path fill="#EA4335" d="M12 10.2v3.6h5.2c-.2 1.2-1.4 3.6-5.2 3.6-3.1 0-5.6-2.6-5.6-5.8S8.9 6.8 12 6.8c1.8 0 2.9.8 3.6 1.5l2.4-2.3C17.2 4 14.8 3 12 3 7.6 3 4 6.6 4 11s3.6 8 8 8c4.6 0 7-3.2 7-7.7 0-.5 0-.9-.1-1.1H12z"/></svg>'
_MS_SVG = '<svg viewBox="0 0 24 24" width="18" height="18" xmlns="http://www.w3.org/2000/svg"><rect x="2" y="2" width="9" height="9" fill="#F35325"/><rect x="13" y="2" width="9" height="9" fill="#81BC06"/><rect x="2" y="13" width="9" height="9" fill="#05A6F0"/><rect x="13" y="13" width="9" height="9" fill="#FFBA08"/></svg>'

PROVIDERS: dict[str, dict[str, Any]] = {}
if settings.google_client_id and settings.google_client_secret:
    PROVIDERS["google"] = {
        "icon": _GOOGLE_SVG,
        "server_metadata_url": "https://accounts.google.com/.well-known/openid-configuration",
        "client_kwargs": {"scope": "openid email profile", "code_challenge_method": "S256"},
    }
if settings.microsoft_client_id and settings.microsoft_client_secret:
    PROVIDERS["microsoft"] = {
        "icon": _MS_SVG,
        "server_metadata_url": f"https://login.microsoftonline.com/{settings.ms_tenant_id}/v2.0/.well-known/openid-configuration",
        "client_kwargs": {
            "scope": "openid email profile offline_access User.Read",
            "code_challenge_method": "S256",
        },
    }

for name, cfg in PROVIDERS.items():
    oauth.register(
        name=name,
        client_id=getattr(settings, f"{name}_client_id"),
        client_secret=getattr(settings, f"{name}_client_secret"),
        server_metadata_url=cfg["server_metadata_url"],
        client_kwargs=cfg["client_kwargs"],
    )


def _load_private_key():
    """Load the JWT signing key directly from SSM to preserve PEM format."""
    ssm = boto3.client("ssm", region_name=settings.aws_region)
    resp = ssm.get_parameter(
        Name="/agentops/jwt-private-key-pem",
        WithDecryption=True,
    )
    raw = resp["Parameter"]["Value"]
    return ECKey.import_key(raw)


SIGNING_KEY = _load_private_key()
PUBLIC_KEY = ECKey.import_key(SIGNING_KEY.as_dict(private=False))
PUBLIC_JWK = PUBLIC_KEY.as_dict(private=False)
PUBLIC_JWK["kid"] = settings.jwt_kid
PUBLIC_JWKS = {"keys": [PUBLIC_JWK]}


def mint_access_token(identity: dict[str, Any]) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": "agentops",
        "aud": "agentops",
        "sub": str(identity["sub"]),
        "provider": identity["provider"],
        "email": identity.get("email"),
        "name": identity.get("name"),
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_ttl_seconds),
        "jti": uuid.uuid4().hex,
    }
    header = {"alg": settings.jwt_alg, "kid": settings.jwt_kid}
    return joserfc_jwt.encode(header, claims, SIGNING_KEY, algorithms=[settings.jwt_alg])


def _identity_from_userinfo(provider: str, userinfo: dict[str, Any]) -> dict[str, Any]:
    sub = userinfo.get("sub") or userinfo.get("oid")
    email = userinfo.get("email") or userinfo.get("mail") or userinfo.get("userPrincipalName")
    name = userinfo.get("name") or userinfo.get("displayName") or userinfo.get("login")
    tenant = userinfo.get("tid") or userinfo.get("tenantId")
    return {
        "provider": provider,
        "sub": str(sub) if sub else None,
        "email": email,
        "name": name,
        "tenant": str(tenant).strip().lower() if isinstance(tenant, str) else None,
    }


# ── Auth Endpoints ──────────────────────────────────────────────────
@router.get("/auth/login", response_class=HTMLResponse)
async def login_page():
    btns = []
    for name, cfg in PROVIDERS.items():
        btns.append(
            f"<a href='/auth/login/start/{name}' class='w-full inline-flex items-center justify-center border rounded py-2 px-3 mb-3'>"
            f"{cfg['icon']} <span style='margin-left:8px'>Continue with {name.capitalize()}</span></a>"
        )
    body = (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<link href='https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css' rel='stylesheet'>"
        "<title>Sign in</title></head><body class='bg-gray-50 min-h-screen flex items-center justify-center'>"
        f"<div class='max-w-md w-full p-6'><div class='bg-white p-6 rounded shadow'><h1 class='text-xl font-semibold mb-3'>Sign in</h1>{''.join(btns)}</div></div></body></html>"
    )
    return HTMLResponse(body)


@router.get("/auth/login/start/{provider}")
async def login_start(request: Request, provider: str):
    provider = provider.strip().lower()
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Provider not enabled")
    client = oauth.create_client(provider)
    if client is None:
        raise HTTPException(status_code=500, detail="OAuth client unavailable")
    redirect_uri = f"{str(request.base_url).rstrip('/')}/auth/callback/{provider}"
    sess = request.session
    sess["oauth_provider"] = provider
    sess["oauth_state"] = uuid.uuid4().hex
    try:
        return await client.authorize_redirect(request, redirect_uri, state=sess["oauth_state"])
    except OAuthError as exc:
        log.warning("oauth redirect failed: %s", exc)
        raise HTTPException(status_code=502, detail="OAuth initiation failed") from exc


@router.get("/auth/callback/{provider}")
async def callback(request: Request, provider: str):
    provider = provider.strip().lower()
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Provider not enabled")
    client = oauth.create_client(provider)
    if client is None:
        raise HTTPException(status_code=500, detail="OAuth client unavailable")
    sess = request.session
    stored_state = sess.pop("oauth_state", None)
    request_state = request.query_params.get("state")
    if not stored_state or stored_state != request_state:
        log.warning("state mismatch for %s", provider)
        return RedirectResponse(url="/auth/login?error=state_mismatch", status_code=302)

    token = None
    try:
        token = await client.authorize_access_token(request)
    except OAuthError:
        code = request.query_params.get("code")
        if code:
            token = await _manual_token_exchange(provider, client, code)

    if not token or not isinstance(token, dict):
        return RedirectResponse(url="/auth/login?error=oauth", status_code=302)

    access_token = token.get("access_token")
    id_token_str = token.get("id_token")

    userinfo = {}
    if id_token_str:
        userinfo = _decode_id_token_safe(id_token_str)

    if not userinfo and access_token and provider == "microsoft":
        try:
            async with httpx.AsyncClient(timeout=10.0) as h:
                resp = await h.get(
                    "https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,userPrincipalName,tenantId",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if resp.status_code == 200:
                    userinfo = resp.json()
        except Exception:
            pass

    identity = _identity_from_userinfo(provider, userinfo)
    if not identity.get("sub") or not identity.get("email"):
        return RedirectResponse(url="/auth/login?error=identity", status_code=302)

    jwt_token = mint_access_token(identity)

    body = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        "<script>"
        f"localStorage.setItem('app_jwt', {json.dumps(jwt_token)});"
        f"window.location.replace({json.dumps('/')});"
        "</script></body></html>"
    )
    return HTMLResponse(body)


async def _manual_token_exchange(provider: str, client, code: str) -> dict[str, Any]:
    token_endpoint = client.server_metadata.get("token_endpoint")
    if not token_endpoint and provider == "microsoft":
        token_endpoint = (
            f"https://login.microsoftonline.com/{settings.ms_tenant_id}/oauth2/v2.0/token"
        )
    if not token_endpoint:
        return {}
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": f"{client.metadata.get('redirect_uri', '')!s}",
    }
    if provider == "google":
        data["client_id"] = settings.google_client_id
        if settings.google_client_secret:
            data["client_secret"] = settings.google_client_secret
    elif provider == "microsoft":
        data["client_id"] = settings.microsoft_client_id
        if settings.microsoft_client_secret:
            data["client_secret"] = settings.microsoft_client_secret
    try:
        async with httpx.AsyncClient(timeout=15.0) as h:
            resp = await h.post(token_endpoint, data=data, headers={"Accept": "application/json"})
            resp.raise_for_status()
            return resp.json()
    except Exception:
        log.error("manual token exchange failed")
        return {}


def _decode_id_token_safe(token_str: str) -> dict[str, Any]:
    try:
        token = joserfc_jwt.decode(token_str, PUBLIC_KEY, algorithms=[settings.jwt_alg])
        return dict(token.claims)
    except Exception:
        import base64

        parts = token_str.split(".")
        if len(parts) >= 2:
            payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
            try:
                return json.loads(base64.urlsafe_b64decode(payload_b64))
            except Exception:
                pass
    return {}


@router.get("/auth/me")
async def me(request: Request):
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    try:
        claims = await verify_access_token(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    return JSONResponse({"authenticated": True, "user": claims})


@router.get("/auth/logout", response_class=HTMLResponse)
async def logout(request: Request):
    try:
        request.session.clear()
    except Exception:
        pass
    return HTMLResponse(
        "<script>localStorage.removeItem('app_jwt');window.location.replace('/');</script>"
    )


@router.get("/.well-known/jwks.json")
async def jwks():
    return JSONResponse(PUBLIC_JWKS)


# ── Chat WebSocket ──────────────────────────────────────────────────
@router.websocket("/ws/chat/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str):
    await websocket.accept()

    token = websocket.query_params.get("token", "")
    user_id = f"anon:{session_id}"
    if token and token != "null":
        try:
            claims = await verify_access_token(token)
            user_id = f"{claims['provider']}#{claims['sub']}"
        except Exception:
            pass

    if not check_rate_limit(user_id):
        await websocket.close(code=4002, reason="Rate limited")
        return

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
                        "WARN", "Failed to load previous state, starting fresh", run_id=run_id
                    )

            if previous_state:
                state = dict(previous_state)
                state["messages"] = [*state.get("messages", []), HumanMessage(content=query)]
                state["query_text"] = query
                state["run_id"] = run_id
                state["action_taken"] = False
                state["tool_results"] = []
                state["final_response"] = None
                state["error"] = None
                if data.get("user_id"):
                    state["user_id"] = data["user_id"]
            else:
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


# ── Admin Endpoints ─────────────────────────────────────────────────
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
