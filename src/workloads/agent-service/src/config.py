"""
Centralised configuration for the Agent Service.
All settings are read from environment variables - no .env file dependency.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import boto3
import dspy
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("agent-service.config")


class Settings(BaseSettings):
    """Application settings loaded from environment variables only."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    # -- LLM --------------------------------------------------------
    llm_safeguard_model: str = "bedrock/meta.llama3-8b-instruct-v1:0"
    llm_resolver_model: str = "bedrock/meta.llama3-8b-instruct-v1:0"

    llm_safeguard_temperature: float = 0.0
    llm_safeguard_max_tokens: int = 1024

    llm_resolver_temperature: float = 0.2
    llm_resolver_max_tokens: int = 2048

    # -- Agent behaviour --------------------------------------------
    urgency_escalate_threshold: int = 8
    max_auto_resolve_amount: float = 10000.0
    max_wallet_credit_amount: float = 500.0
    multi_turn_enabled: bool = True
    max_conversation_turns: int = 20

    # -- MCP Server -------------------------------------------------
    mcp_server_url: str = "http://localhost:8001/mcp"

    # -- PostgreSQL -------------------------------------------------
    database_url: str = "postgresql://agentops:localdev@localhost:5432/kestral"
    pool_min_size: int = 5
    pool_max_size: int = 25

    # -- Policy Search (Bedrock + S3) -------------------------------
    bedrock_region: str = "ap-south-1"
    bedrock_embed_model: str = "amazon.titan-embed-text-v2:0"
    aws_region: str = "ap-south-1"
    embeddings_bucket: str = "agentops-embeddings-temp-xyz"
    embeddings_key: str = "embeddings.json"

    # -- Server -----------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    service_version: str = "1.0.0"
    deployment_environment: str = "local"

    # -- Auth (OIDC) -------------------------------------------------
    jwt_alg: str = "ES256"
    jwt_kid: str = "agentops-jwt-key"
    jwt_private_key_pem: str = ""
    jwt_ttl_seconds: int = 900
    session_secret: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    ms_tenant_id: str = "common"
    admin_allowed_google_domains: set[str] = set()
    admin_allowed_microsoft_tenants: set[str] = set()

    # -- Rate Limiting ------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = 10
    rate_limit_window_seconds: int = 60


settings = Settings()


def _load_ssm_parameter(name: str, decrypt: bool = False) -> str | None:
    """Fetch a parameter from SSM Parameter Store, returning None on failure."""
    try:
        ssm = boto3.client("ssm", region_name=settings.aws_region)
        resp = ssm.get_parameter(Name=name, WithDecryption=decrypt)
        return resp["Parameter"]["Value"]
    except Exception as exc:
        log.debug("SSM parameter %s not available: %s", name, exc)
        return None


@lru_cache(maxsize=1)
def load_ssm_parameters() -> None:
    """
    Populate empty settings from SSM Parameter Store.
    Called once at startup; values already set via env vars are NOT overwritten.
    """
    overrides: dict[str, str | set[str]] = {}

    def _apply(key: str, ssm_name: str, decrypt: bool = False) -> None:
        current = getattr(settings, key, None)
        if current is not None and current != "" and current != set():
            return  # already set via env
        value = _load_ssm_parameter(ssm_name, decrypt=decrypt)
        if value is not None:
            overrides[key] = value

    _apply("jwt_private_key_pem", "/agentops/jwt-private-key-pem", decrypt=True)
    _apply("jwt_kid", "/agentops/jwt-kid")
    _apply("session_secret", "/agentops/session-secret", decrypt=True)
    _apply("google_client_id", "/agentops/google-client-id", decrypt=True)
    _apply("google_client_secret", "/agentops/google-client-secret", decrypt=True)
    _apply("microsoft_client_id", "/agentops/microsoft-client-id", decrypt=True)
    _apply("microsoft_client_secret", "/agentops/microsoft-client-secret", decrypt=True)
    _apply("ms_tenant_id", "/agentops/ms-tenant-id")

    # Allowed admin domains / tenants
    _apply("admin_allowed_google_domains", "/agentops/admin-allowed-google-domains")
    _apply("admin_allowed_microsoft_tenants", "/agentops/admin-allowed-microsoft-tenants")

    for k, v in overrides.items():
        if k in ("admin_allowed_google_domains", "admin_allowed_microsoft_tenants"):
            # SSM returns comma-separated strings; convert to set
            if isinstance(v, str):
                v = {s.strip().lower() for s in v.split(",") if s.strip()}
        setattr(settings, k, v)


def create_safeguard_lm() -> dspy.LM:
    return dspy.LM(
        model=settings.llm_safeguard_model,
        temperature=settings.llm_safeguard_temperature,
        max_tokens=settings.llm_safeguard_max_tokens,
    )


def create_resolver_lm() -> dspy.LM:
    return dspy.LM(
        model=settings.llm_resolver_model,
        temperature=settings.llm_resolver_temperature,
        max_tokens=settings.llm_resolver_max_tokens,
    )
