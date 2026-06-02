# auth/auth_middleware.py — Final
"""
Middleware that extracts user identity from JWT if present.
"""

from __future__ import annotations

import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from auth.jwt_utils import verify_access_token

log = logging.getLogger("agent-service.auth")


class IdentityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if token:
            try:
                claims = await verify_access_token(token)
                request.state.user_claims = claims
            except Exception:
                pass  # ignore invalid tokens for non-admin routes

        return await call_next(request)
