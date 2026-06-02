"""
asyncpg connection pool factory and all SQL query functions.

Only the queries needed by the pragmatic agent:
- lookup_customer
- get_recent_orders (with product info)
- create_ticket (with summary and suggested_action)
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg
from config import settings


async def create_pool() -> asyncpg.Pool:
    """Create an asyncpg pool tuned for PgBouncer transaction pooling."""
    return await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
        command_timeout=settings.pool_command_timeout,
    )


async def migrate_tickets_table(pool: asyncpg.Pool) -> None:
    """Add columns needed for AI-generated ticket summaries (idempotent)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS summary TEXT")
            await conn.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS suggested_action TEXT")


async def get_user_by_email(pool: asyncpg.Pool, email: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, full_name, email, phone, language_pref, segment, created_at "
                "FROM users WHERE email = $1",
                email,
            )
    return dict(row) if row else None


async def get_user_by_phone(pool: asyncpg.Pool, phone: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, full_name, email, phone, language_pref, segment, created_at "
                "FROM users WHERE phone = $1",
                phone,
            )
    return dict(row) if row else None


async def get_recent_orders(
    pool: asyncpg.Pool, user_id: str, limit: int = 5
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """SELECT o.id, o.user_id, o.product_id, o.status, o.quantity,
                   o.amount, o.discount_amount, o.shipping_amount, o.cod_fee,
                   o.payment_method, o.shipping_address, o.pincode, o.city,
                   o.order_date, o.delivery_date, o.promised_delivery_date,
                   o.is_delayed, o.delivery_attempts, o.tracking_number, o.notes,
                   p.name AS product_name, p.category, p.return_window_days,
                   p.is_returnable
                   FROM orders o
                   JOIN products p ON o.product_id = p.id
                   WHERE o.user_id = $1
                   ORDER BY o.order_date DESC LIMIT $2""",
                user_id,
                limit,
            )
    return [dict(r) for r in rows]


async def insert_ticket(
    pool: asyncpg.Pool,
    user_id: str,
    query_text: str,
    classification: dict,
    priority: str,
    assigned_team: str = "general_support",
    summary: str | None = None,
    suggested_action: str | None = None,
) -> str:
    """Create a new ticket. Returns the ticket UUID."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """INSERT INTO tickets
                   (id, user_id, query_text, classification, resolution_type, status,
                    priority, assigned_team, source, created_at, updated_at,
                    summary, suggested_action)
                   VALUES (gen_random_uuid(), $1, $2, $3::jsonb, 'escalated', 'pending_human',
                           $4, $5, 'chat', NOW(), NOW(), $6, $7)
                   RETURNING id""",
                user_id,
                query_text,
                json.dumps(classification),
                priority,
                assigned_team,
                summary,
                suggested_action,
            )
    return str(row["id"])
