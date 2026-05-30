"""
Centralised configuration from environment variables.
No .env file — strong defaults for local development.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables only."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    # ── PostgreSQL ────────────────────────────────────────────────
    database_url: str = "postgresql://agentops:localdev@localhost:5432/kestral"
    pool_min_size: int = 2
    pool_max_size: int = 10
    pool_command_timeout: float = 10.0

    # ── Business Rules ────────────────────────────────────────────
    max_wallet_credit_amount: Decimal = Decimal("500.00")

    # ── Server ────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8001
    log_level: str = "INFO"

    # ── Metadata ──────────────────────────────────────────────────
    service_version: str = "1.0.0"
    deployment_environment: str = "local"


settings = Settings()
