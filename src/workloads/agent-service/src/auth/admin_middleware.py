"""
Middleware that restricts /admin/* routes to authorised users based on
OIDC provider domain / tenant.
"""

from __future__ import annotations

import logging

from config import settings
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from auth.jwt_utils import verify_access_token

log = logging.getLogger("agent-service.auth")


class AdminAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/admin"):
            return await call_next(request)

        # Health / readiness probes are always allowed
        if request.url.path in {"/healthz", "/readyz"}:
            return await call_next(request)

        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not token:
            return JSONResponse(status_code=401, content={"detail": "Missing authentication token"})

        try:
            claims = await verify_access_token(token)
        except Exception:
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

        email = claims.get("email") or ""
        provider = claims.get("provider", "")
        tenant = claims.get("tenant", "")

        authorised = False
        if provider == "google" and "@" in email:
            domain = email.rsplit("@", 1)[-1].lower()
            if domain in settings.admin_allowed_google_domains:
                authorised = True
        elif provider == "microsoft":
            if tenant and tenant.lower() in settings.admin_allowed_microsoft_tenants:
                authorised = True

        if not authorised:
            log.warning("Admin access denied for %s", email)
            return JSONResponse(status_code=403, content={"detail": "Admin access denied"})

        # Attach user info to request state for later use
        request.state.user_claims = claims
        return await call_next(request)
