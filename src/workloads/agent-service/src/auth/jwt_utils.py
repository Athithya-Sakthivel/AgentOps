"""
JWT verification using local public key (no external auth service).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from config import settings
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import ECKey

log = logging.getLogger("agent-service.auth")

_public_key: ECKey | None = None


def _get_public_key() -> ECKey:
    global _public_key
    if _public_key is not None:
        return _public_key
    private_key = ECKey.import_key(settings.jwt_private_key_pem)
    pub_dict = private_key.as_dict(private=False)
    pub_dict["kid"] = settings.jwt_kid
    _public_key = ECKey.import_key(pub_dict)
    return _public_key


async def verify_access_token(token_str: str) -> dict[str, Any]:
    """Verify a JWT and return its claims."""
    if not token_str:
        raise ValueError("Empty token")

    public_key = _get_public_key()
    try:
        token = joserfc_jwt.decode(token_str, public_key, algorithms=[settings.jwt_alg])
    except Exception as exc:
        log.warning("JWT verification failed: %s", exc)
        raise ValueError("Invalid token") from exc

    claims = dict(token.claims)
    if claims.get("exp", 0) < time.time():
        raise ValueError("Token expired")

    return claims
