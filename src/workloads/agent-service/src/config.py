"""
Centralised configuration for the Agent Service.
All settings are read from environment variables - no .env file dependency.
"""

from __future__ import annotations

import dspy
from pydantic_settings import BaseSettings, SettingsConfigDict


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


settings = Settings()


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
