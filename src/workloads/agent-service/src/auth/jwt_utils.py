"""
JWT verification using remote JWKS (from the stateless OIDC auth service).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from config import settings
from joserfc import jwt
from joserfc.jwk import KeySet

log = logging.getLogger("agent-service.auth")

_jwks_cache: KeySet | None = None


async def _get_jwks() -> KeySet:
    global _jwks_cache
    if _jwks_cache is not None:
        return _jwks_cache

    url = f"{settings.auth_service_url.rstrip('/')}/.well-known/jwks.json"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        _jwks_cache = KeySet.import_key_set(data["keys"])
        log.info("JWKS loaded from %s", url)
    return _jwks_cache


async def verify_access_token(token_str: str) -> dict[str, Any]:
    """Verify a JWT issued by the auth service and return its claims.

    Raises:
        ValueError: if the token is invalid or expired.
    """
    if not token_str:
        raise ValueError("Empty token")

    jwks = await _get_jwks()
    try:
        token = jwt.decode(token_str, jwks)
    except Exception as exc:
        log.warning("JWT verification failed: %s", exc)
        raise ValueError("Invalid token") from exc

    claims = dict(token.claims)

    # Validate standard claims
    now = __import__("time").time()
    if claims.get("exp", 0) < now:
        raise ValueError("Token expired")

    return claims
