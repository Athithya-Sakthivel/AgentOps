"""
Centralised configuration for the Agent Service.
All secrets are loaded from SSM Parameter Store at startup.
No .env file dependency.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import boto3
import dspy
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("agent-service.config")


class Settings(BaseSettings):
    """Application settings — defaults only, overridden by SSM."""

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

    # -- Auth (OIDC) — loaded from SSM at startup -------------------
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


def _fetch_ssm(name: str, decrypt: bool = False) -> str:
    ssm = boto3.client("ssm", region_name=settings.aws_region)
    resp = ssm.get_parameter(Name=name, WithDecryption=decrypt)
    return resp["Parameter"]["Value"]


@lru_cache(maxsize=1)
def load_ssm_parameters() -> None:
    """Fetch secrets from SSM and override settings."""
    if settings.deployment_environment == "local" and settings.jwt_private_key_pem:
        log.info("Skipping SSM load — jwt_private_key_pem already set via env")
        return

    log.info("Loading secrets from SSM Parameter Store...")

    ssm_params = [
        ("jwt_private_key_pem", "/agentops/jwt-private-key-pem", True),
        ("jwt_kid", "/agentops/jwt-kid", False),
        ("session_secret", "/agentops/session-secret", True),
        ("google_client_id", "/agentops/google-client-id", True),
        ("google_client_secret", "/agentops/google-client-secret", True),
        ("microsoft_client_id", "/agentops/microsoft-client-id", True),
        ("microsoft_client_secret", "/agentops/microsoft-client-secret", True),
        ("ms_tenant_id", "/agentops/ms-tenant-id", False),
        ("admin_allowed_google_domains", "/agentops/admin-allowed-google-domains", False),
        ("admin_allowed_microsoft_tenants", "/agentops/admin-allowed-microsoft-tenants", False),
    ]

    for key, ssm_name, decrypt in ssm_params:
        try:
            value = _fetch_ssm(ssm_name, decrypt=decrypt)
            if key in ("admin_allowed_google_domains", "admin_allowed_microsoft_tenants"):
                value = {s.strip().lower() for s in value.split(",") if s.strip()}
            setattr(settings, key, value)
            log.info("  Loaded %s from %s", key, ssm_name)
        except Exception as exc:
            log.error("  FAILED to load %s from %s: %s", key, ssm_name, exc)
            raise RuntimeError(f"Missing SSM parameter: {ssm_name}") from exc


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
