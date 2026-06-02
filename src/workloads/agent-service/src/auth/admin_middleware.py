"""
Middleware that restricts /admin/* API routes to authorised users.
The /admin HTML page itself is publicly accessible.
"""

from __future__ import annotations

import logging

from config import settings
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from auth.jwt_utils import verify_access_token

log = logging.getLogger("agent-service.auth")


class AdminAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Only intercept /admin paths
        if not request.url.path.startswith("/admin"):
            return await call_next(request)

        # Allow the HTML page itself (GET /admin or GET /admin/)
        if request.url.path in ("/admin", "/admin/") and request.method == "GET":
            return await call_next(request)

        # Allow probes
        if request.url.path in {"/healthz", "/readyz"}:
            return await call_next(request)

        # Everything else under /admin requires a valid token
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not token:
            # Browser request → redirect to login
            accept = request.headers.get("Accept", "")
            if "text/html" in accept:
                return RedirectResponse(url="/auth/login", status_code=302)
            return JSONResponse(status_code=401, content={"detail": "Missing authentication token"})

        try:
            claims = await verify_access_token(token)
        except Exception:
            accept = request.headers.get("Accept", "")
            if "text/html" in accept:
                return RedirectResponse(url="/auth/login", status_code=302)
            return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})

        email = claims.get("email") or ""
        provider = claims.get("provider", "")
        tenant = claims.get("tenant", "")

        authorised = False
        if provider == "google" and "@" in email:
            domain = email.rsplit("@", 1)[-1].lower()
            allowed = settings.admin_allowed_google_domains
            if isinstance(allowed, str):
                allowed = [d.strip() for d in allowed.split(",")]
            if domain in allowed:
                authorised = True
        elif provider == "microsoft":
            allowed_tenants = settings.admin_allowed_microsoft_tenants
            if isinstance(allowed_tenants, str):
                allowed_tenants = [t.strip() for t in allowed_tenants.split(",")]
            if tenant and tenant.lower() in allowed_tenants:
                authorised = True

        if not authorised:
            log.warning("Admin access denied for %s", email)
            return JSONResponse(status_code=403, content={"detail": "Admin access denied"})

        request.state.user_claims = claims
        return await call_next(request)
