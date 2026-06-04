"""
Idempotent database seeder for Kestral ticket-triage system.

Creates and populates all tables needed for local development and testing:
  users, products, orders, billing, tickets

Connects to a remote RDS PostgreSQL instance.  Connection string is read from
the environment variable DATABASE_URL (e.g., output of OpenTofu).

"""

import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://agentops:localdev@localhost:5432/kestral",
)

JSON_FILES = {
    "users": SCRIPT_DIR / "users.json",
    "products": SCRIPT_DIR / "products.json",
    "orders": SCRIPT_DIR / "orders.json",
    "billing": SCRIPT_DIR / "billing.json",
    "tickets": SCRIPT_DIR / "tickets.json",
    "dspy_seed": SCRIPT_DIR / "seed_tickets_dspy.json",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_json(path: Path) -> list:
    if not path.exists():
        print(f"  {path.name} not found - skipping.")
        return []
    with open(path) as f:
        return json.load(f)


def safe_uuid(raw: Any) -> uuid.UUID:
    if isinstance(raw, uuid.UUID):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode()
    return uuid.UUID(str(raw).strip())


def parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_dec(val: Any) -> Decimal:
    return Decimal(str(val))


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id = Column(UUID(as_uuid=True), primary_key=True)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    phone = Column(String(20), nullable=False)
    language_pref = Column(String(5), nullable=False, default="en")
    segment = Column(String(20), nullable=False, default="new")
    created_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("idx_users_segment", "segment"),)


class Product(Base):
    __tablename__ = "products"
    id = Column(UUID(as_uuid=True), primary_key=True)
    name = Column(String(255), nullable=False)
    category = Column(String(50), nullable=False)
    subcategory = Column(String(100))
    price = Column(Numeric(10, 2), nullable=False)
    return_window_days = Column(Integer, nullable=False, default=10)
    warranty_months = Column(Integer, nullable=False, default=12)
    is_returnable = Column(Boolean, nullable=False, default=True)
    is_express_eligible = Column(Boolean, nullable=False, default=False)
    stock_quantity = Column(Integer, nullable=False, default=100)

    __table_args__ = (Index("idx_products_category", "category"),)


class Order(Base):
    __tablename__ = "orders"
    id = Column(UUID(as_uuid=True), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    status = Column(String(20), nullable=False, default="placed")
    quantity = Column(Integer, nullable=False, default=1)
    amount = Column(Numeric(10, 2), nullable=False)
    discount_amount = Column(Numeric(10, 2), default=0)
    shipping_amount = Column(Numeric(10, 2), default=0)
    cod_fee = Column(Numeric(10, 2), default=0)
    payment_method = Column(String(20), nullable=False)
    shipping_address = Column(JSONB, nullable=False)
    pincode = Column(String(10), nullable=False, index=True)
    city = Column(String(100), nullable=False)
    order_date = Column(DateTime(timezone=True), nullable=False)
    delivery_date = Column(DateTime(timezone=True))
    promised_delivery_date = Column(DateTime(timezone=True))
    is_delayed = Column(Boolean, default=False)
    delivery_attempts = Column(Integer, default=0)
    tracking_number = Column(String(50))
    notes = Column(Text)

    __table_args__ = (
        Index("idx_orders_status", "status"),
        Index("idx_orders_order_date", "order_date"),
    )


class Billing(Base):
    __tablename__ = "billing"
    id = Column(UUID(as_uuid=True), primary_key=True)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    transaction_type = Column(String(20), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    status = Column(String(20), default="pending")
    refund_eligible = Column(Boolean, default=False)
    refund_reason = Column(String(50))
    payment_gateway = Column(String(50))
    gateway_transaction_id = Column(String(100))
    transaction_date = Column(DateTime(timezone=True), nullable=False)
    completed_date = Column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_billing_status", "status"),
        Index("idx_billing_transaction_type", "transaction_type"),
    )


class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(UUID(as_uuid=True), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True, index=True)
    query_text = Column(Text, nullable=False)
    classification = Column(JSONB)
    resolution_type = Column(String(20))
    status = Column(String(20), nullable=False, default="open")
    priority = Column(String(10), default="medium")
    assigned_team = Column(String(100))
    assigned_agent = Column(String(100))
    resolution_summary = Column(Text)
    source = Column(String(20), default="chat")
    language = Column(String(5), default="en")
    created_at = Column(DateTime(timezone=True), nullable=False)
    resolved_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_tickets_status", "status"),
        Index("idx_tickets_priority", "priority"),
        Index("idx_tickets_assigned_team", "assigned_team"),
        Index("idx_tickets_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# Seed logic
# ---------------------------------------------------------------------------
async def seed(engine, session_factory):
    print("Dropping existing tables...")
    async with engine.begin() as conn:
        for table in (
            Billing.__table__,
            Ticket.__table__,
            Order.__table__,
            Product.__table__,
            User.__table__,
        ):
            await conn.run_sync(table.drop, checkfirst=True)

    print("Creating tables...")
    async with engine.begin() as conn:
        for table in (
            User.__table__,
            Product.__table__,
            Order.__table__,
            Billing.__table__,
            Ticket.__table__,
        ):
            await conn.run_sync(table.create, checkfirst=True)
    print("Schema ready.\n")

    print("Loading JSON files from src/offline/simulate_company...")
    users_j = load_json(JSON_FILES["users"])
    products_j = load_json(JSON_FILES["products"])
    orders_j = load_json(JSON_FILES["orders"])
    billing_j = load_json(JSON_FILES["billing"])
    tickets_j = load_json(JSON_FILES["tickets"])
    dspy_j = load_json(JSON_FILES["dspy_seed"])
    print(f"   users={len(users_j)}  products={len(products_j)}  orders={len(orders_j)}")
    print(f"   billing={len(billing_j)}  tickets={len(tickets_j)}  dspy={len(dspy_j)}\n")

    async with session_factory() as session:
        user_ids, prod_ids, order_ids, ticket_ids = set(), set(), set(), set()
        bill_count = 0

        # Users
        for u in users_j:
            uid = safe_uuid(u["id"])
            session.add(
                User(
                    id=uid,
                    full_name=u["full_name"],
                    email=u["email"],
                    phone=u["phone"],
                    language_pref=u.get("language_pref", "en"),
                    segment=u.get("segment", "new"),
                    created_at=parse_dt(u["created_at"]),
                )
            )
            user_ids.add(uid)
        await session.flush()
        print(f"   {len(user_ids)} users")

        # Products
        for p in products_j:
            pid = safe_uuid(p["id"])
            session.add(
                Product(
                    id=pid,
                    name=p["name"],
                    category=p["category"],
                    subcategory=p.get("subcategory"),
                    price=parse_dec(p["price"]),
                    return_window_days=p.get("return_window_days", 10),
                    warranty_months=p.get("warranty_months", 12),
                    is_returnable=p.get("is_returnable", True),
                    is_express_eligible=p.get("is_express_eligible", False),
                    stock_quantity=p.get("stock_quantity", 100),
                )
            )
            prod_ids.add(pid)
        await session.flush()
        print(f"   {len(prod_ids)} products")

        # Orders
        for o in orders_j:
            oid, uid, pid = safe_uuid(o["id"]), safe_uuid(o["user_id"]), safe_uuid(o["product_id"])
            if uid not in user_ids or pid not in prod_ids:
                continue
            session.add(
                Order(
                    id=oid,
                    user_id=uid,
                    product_id=pid,
                    status=o.get("status", "placed"),
                    quantity=o.get("quantity", 1),
                    amount=parse_dec(o["amount"]),
                    discount_amount=parse_dec(o.get("discount_amount", 0)),
                    shipping_amount=parse_dec(o.get("shipping_amount", 0)),
                    cod_fee=parse_dec(o.get("cod_fee", 0)),
                    payment_method=o["payment_method"],
                    shipping_address=o["shipping_address"],
                    pincode=o.get("pincode", "000000"),
                    city=o.get("city", "Unknown"),
                    order_date=parse_dt(o["order_date"]),
                    delivery_date=parse_dt(o.get("delivery_date")),
                    promised_delivery_date=parse_dt(o.get("promised_delivery_date")),
                    is_delayed=o.get("is_delayed", False),
                    delivery_attempts=o.get("delivery_attempts", 0),
                    tracking_number=o.get("tracking_number"),
                    notes=o.get("notes"),
                )
            )
            order_ids.add(oid)
        await session.flush()
        print(f"   {len(order_ids)} orders")

        # Billing
        for b in billing_j:
            oid, uid = safe_uuid(b["order_id"]), safe_uuid(b["user_id"])
            if oid not in order_ids or uid not in user_ids:
                continue
            session.add(
                Billing(
                    id=safe_uuid(b["id"]),
                    order_id=oid,
                    user_id=uid,
                    transaction_type=b["transaction_type"],
                    amount=parse_dec(b["amount"]),
                    status=b.get("status", "pending"),
                    refund_eligible=b.get("refund_eligible", False),
                    refund_reason=b.get("refund_reason"),
                    payment_gateway=b.get("payment_gateway"),
                    gateway_transaction_id=b.get("gateway_transaction_id"),
                    transaction_date=parse_dt(b["transaction_date"]),
                    completed_date=parse_dt(b.get("completed_date")),
                )
            )
            bill_count += 1
        await session.flush()
        print(f"   {bill_count} billing rows")

        # Tickets
        for t in tickets_j:
            tid, uid = safe_uuid(t["id"]), safe_uuid(t["user_id"])
            oid = safe_uuid(t["order_id"]) if t.get("order_id") else None
            if uid not in user_ids or (oid and oid not in order_ids):
                continue
            session.add(
                Ticket(
                    id=tid,
                    user_id=uid,
                    order_id=oid,
                    query_text=t["query_text"],
                    classification=t.get("classification"),
                    resolution_type=t.get("resolution_type"),
                    status=t.get("status", "open"),
                    priority=t.get("priority", "medium"),
                    assigned_agent=t.get("assigned_agent"),
                    resolution_summary=t.get("resolution_summary"),
                    source=t.get("source", "chat"),
                    language=t.get("language", "en"),
                    created_at=parse_dt(t["created_at"]),
                    resolved_at=parse_dt(t.get("resolved_at")),
                    updated_at=parse_dt(t.get("updated_at")),
                )
            )
            ticket_ids.add(tid)
        await session.flush()
        print(f"   {len(ticket_ids)} tickets" if ticket_ids else "   No tickets seeded")

        # DSPy validation
        valid_intents = {
            "return_request",
            "refund_status",
            "delayed_delivery",
            "wrong_item_delivered",
            "damaged_product",
            "cancellation_request",
            "warranty_claim",
            "defective_product",
            "escalation_request",
            "delivery_issue",
            "order_status",
            "payment_issue",
        }
        dspy_ok = sum(
            1
            for d in dspy_j
            if all(k in d for k in ("query", "intent", "urgency", "sentiment", "auto_resolvable"))
            and d["intent"] in valid_intents
        )
        print(f"   {dspy_ok}/{len(dspy_j)} DSPy examples valid (not inserted)\n")

        print("Committing...")
        await session.commit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    print(f"Connecting to RDS: {DB_URL.split('@')[1] if '@' in DB_URL else DB_URL}")

    engine = create_async_engine(
        DB_URL.replace("postgresql://", "postgresql+asyncpg://"),
        echo=False,
        pool_size=5,
        max_overflow=2,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        print("Connection successful.\n")
    except Exception as e:
        print(f"[ERROR] Connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    await seed(engine, session_factory)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
