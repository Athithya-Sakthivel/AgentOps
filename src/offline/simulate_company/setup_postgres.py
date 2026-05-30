"""
Idempotent database seeder for Kestral ticket-triage system.

Creates and populates all tables needed for local development and testing:
  users, products, orders, billing, tickets

Uses a local PostgreSQL 18 container (no Kubernetes).
Data persists in /workspace/postgres/ across restarts.

Usage:
    python3 src/offline/simulate_company/setup_postgres.py
"""

import asyncio
import json
import subprocess
import sys
import time
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

POSTGRES_IMAGE = "docker.io/library/postgres:18.4"
CONTAINER_NAME = "kestral-postgres"
HOST_PORT = 5432
CONTAINER_PORT = 5432
POSTGRES_USER = "agentops"
POSTGRES_PASSWORD = "localdev"
POSTGRES_DB = "kestral"
DATA_DIR = Path("/workspace/postgres")

# Postgres 18 requires mount at /var/lib/postgresql (NOT /var/lib/postgresql/data)
CONTAINER_DATA_DIR = "/var/lib/postgresql"

DB_URL = (
    f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@localhost:{HOST_PORT}/{POSTGRES_DB}"
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
# Docker helpers (never throw — return success/failure)
# ---------------------------------------------------------------------------


def docker(args: list[str]) -> tuple[bool, str, str]:
    """
    Run a docker command. Never throws.
    Returns (success, stdout, stderr).
    """
    try:
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return False, "", "docker: command not found"
    except subprocess.TimeoutExpired:
        return False, "", "docker: command timed out"
    except Exception as e:
        return False, "", str(e)


def container_status() -> str:
    """Returns: 'running', 'exited', 'paused', 'restarting', 'created', 'nonexistent'."""
    ok, out, _ = docker(
        ["ps", "-a", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Status}}"]
    )
    if not ok or not out:
        return "nonexistent"
    status_line = out.split("\n")[0]
    if status_line.startswith("Up"):
        return "running"
    if status_line.startswith("Exited"):
        return "exited"
    if status_line.startswith("Paused"):
        return "paused"
    if status_line.startswith("Created"):
        return "created"
    return "restarting"  # Covers "Restarting" and any other state


def force_remove_container() -> bool:
    """Force-remove container regardless of state. Returns True if gone."""
    status = container_status()
    if status == "nonexistent":
        return True

    # Try gentle stop first
    if status == "running":
        docker(["stop", "-t", "5", CONTAINER_NAME])
        time.sleep(2)

    # Force remove (handles running, paused, exited, created, restarting)
    ok, _, err = docker(["rm", "-f", CONTAINER_NAME])
    if not ok:
        print(f"  [WARN] Could not remove container: {err}")
        return False

    # Verify it's gone
    return container_status() == "nonexistent"


def ensure_data_dir() -> None:
    """Create data directory with proper permissions."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Docker may write as a different UID; make directory writable
    DATA_DIR.chmod(0o777)


def port_available() -> bool:
    """Check if HOST_PORT is free."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("localhost", HOST_PORT))
            return True
        except OSError:
            return False


# ---------------------------------------------------------------------------
# PostgreSQL container lifecycle
# ---------------------------------------------------------------------------


def start_postgres() -> None:
    """
    Ensure PostgreSQL container is running.
    Idempotent: if already running, does nothing.
    If a dead container exists, removes and recreates.
    If port is taken by something else, reports clearly.
    """
    ensure_data_dir()
    status = container_status()

    # Already running — nothing to do
    if status == "running":
        print(f"PostgreSQL container '{CONTAINER_NAME}' is already running.")
        return

    # Some other state — clean up and recreate
    if status != "nonexistent":
        print(f"Container '{CONTAINER_NAME}' is in state '{status}'. Cleaning up...")
        if not force_remove_container():
            print("[ERROR] Cannot remove existing container. Aborting.", file=sys.stderr)
            sys.exit(1)
        print("  Cleaned up.")

    # Check port availability
    if not port_available():
        print(f"[ERROR] Port {HOST_PORT} is already in use by another process.", file=sys.stderr)
        print("  Check: lsof -i :5432  or  ss -tlnp | grep 5432", file=sys.stderr)
        sys.exit(1)

    print(f"Creating PostgreSQL container '{CONTAINER_NAME}'...")
    print(f"  Image: {POSTGRES_IMAGE}")
    print(f"  Data:  {DATA_DIR} -> {CONTAINER_DATA_DIR}")

    ok, _, err = docker(
        [
            "run",
            "--name",
            CONTAINER_NAME,
            "--restart",
            "unless-stopped",
            "-d",
            "-p",
            f"{HOST_PORT}:{CONTAINER_PORT}",
            "-e",
            f"POSTGRES_USER={POSTGRES_USER}",
            "-e",
            f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
            "-e",
            f"POSTGRES_DB={POSTGRES_DB}",
            "-v",
            f"{DATA_DIR}:{CONTAINER_DATA_DIR}",
            POSTGRES_IMAGE,
        ]
    )

    if not ok:
        print("\n[ERROR] Docker run failed:", file=sys.stderr)
        print(f"  {err}", file=sys.stderr)
        sys.exit(1)

    # Wait for PostgreSQL to be ready
    print("Waiting for PostgreSQL to accept connections...")
    if not wait_for_postgres(timeout=60):
        print("\n[ERROR] PostgreSQL failed to become ready.", file=sys.stderr)
        print("Container logs (last 40 lines):", file=sys.stderr)
        _, logs, _ = docker(["logs", "--tail", "40", CONTAINER_NAME])
        print(logs, file=sys.stderr)
        sys.exit(1)


def wait_for_postgres(timeout: int = 60) -> bool:
    """
    Poll pg_isready until success or timeout.
    Also monitors container health — if container dies, returns False immediately.
    """
    start = time.time()
    while time.time() - start < timeout:
        # Check container is still alive
        status = container_status()
        if status == "nonexistent":
            return False
        if status not in ("running", "restarting"):
            # Exited or paused — won't become ready
            return False

        ok, out, _ = docker(["exec", CONTAINER_NAME, "pg_isready", "-U", POSTGRES_USER])
        if ok and "accepting connections" in out:
            elapsed = time.time() - start
            print(f"  PostgreSQL ready. (took {elapsed:.1f}s)")
            return True

        time.sleep(1)

    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_json(path: Path) -> list:
    """Load a JSON file, returning empty list if not found."""
    if not path.exists():
        print(f"  {path.name} not found - skipping.")
        return []
    with open(path) as f:
        return json.load(f)


def safe_uuid(raw: Any) -> uuid.UUID:
    """Convert various types to UUID safely."""
    if isinstance(raw, uuid.UUID):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode()
    return uuid.UUID(str(raw).strip())


def parse_dt(val: str | None) -> datetime | None:
    """Parse ISO datetime string, ensuring timezone-aware."""
    if not val:
        return None
    dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_dec(val: Any) -> Decimal:
    """Convert to Decimal safely."""
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
    """Drop existing tables, recreate, and populate with JSON data."""
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

    print("Loading JSON files...")
    users_j = load_json(JSON_FILES["users"])
    products_j = load_json(JSON_FILES["products"])
    orders_j = load_json(JSON_FILES["orders"])
    billing_j = load_json(JSON_FILES["billing"])
    tickets_j = load_json(JSON_FILES["tickets"])
    dspy_j = load_json(JSON_FILES["dspy_seed"])
    print(f"   users={len(users_j)}  products={len(products_j)}  orders={len(orders_j)}")
    print(f"   billing={len(billing_j)}  tickets={len(tickets_j)}  dspy_examples={len(dspy_j)}\n")

    async with session_factory() as session:
        user_ids = set()
        prod_ids = set()
        order_ids = set()
        ticket_ids = set()
        bill_count = 0

        # Users
        print("Inserting users...")
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
        print(f"   {len(user_ids)} users inserted")

        # Products
        print("Inserting products...")
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
        print(f"   {len(prod_ids)} products inserted")

        # Orders
        print("Inserting orders...")
        for o in orders_j:
            oid = safe_uuid(o["id"])
            uid = safe_uuid(o["user_id"])
            pid = safe_uuid(o["product_id"])
            if uid not in user_ids or pid not in prod_ids:
                print(f"   skipping order {oid} (missing FK)")
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
        print(f"   {len(order_ids)} orders inserted")

        # Billing
        print("Inserting billing...")
        for b in billing_j:
            oid = safe_uuid(b["order_id"])
            uid = safe_uuid(b["user_id"])
            if oid not in order_ids or uid not in user_ids:
                print(f"   skipping billing {b['id']} (missing FK)")
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
        print(f"   {bill_count} billing rows inserted")

        # Tickets
        if tickets_j:
            print("Inserting tickets...")
            for t in tickets_j:
                tid = safe_uuid(t["id"])
                uid = safe_uuid(t["user_id"])
                oid = safe_uuid(t["order_id"]) if t.get("order_id") else None
                if uid not in user_ids or (oid and oid not in order_ids):
                    print(f"   skipping ticket {tid} (missing FK)")
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
            print(f"   {len(ticket_ids)} tickets inserted")
        else:
            print("   No tickets.json - skipping")

        # DSPy seed validation (not inserted)
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
        dspy_ok = 0
        for d in dspy_j:
            if all(k in d for k in ("query", "intent", "urgency", "sentiment", "auto_resolvable")):
                if d["intent"] in valid_intents:
                    dspy_ok += 1
        print(f"   {dspy_ok}/{len(dspy_j)} DSPy examples valid (not inserted)\n")

        print("Committing...")
        await session.commit()

    await print_schema(engine)

    print("\n" + "=" * 60)
    print("Seed complete!")
    print(f"   users={len(user_ids)}  products={len(prod_ids)}  orders={len(order_ids)}")
    print(f"   billing={bill_count}  tickets={len(ticket_ids)}  dspy_examples={dspy_ok}")
    print(f"   Data persisted at: {DATA_DIR}")
    print("=" * 60)


async def print_schema(engine):
    """Print columns, indexes, and foreign keys for all tables."""
    async with engine.connect() as conn:
        raw_conn = await conn.get_raw_connection()
        pg_conn = raw_conn.driver_connection

        tables = ["users", "products", "orders", "billing", "tickets", "human_overrides"]

        for tbl in tables:
            cols = await pg_conn.fetch(
                """SELECT column_name, data_type, is_nullable
                   FROM information_schema.columns
                   WHERE table_name = $1 ORDER BY ordinal_position""",
                tbl,
            )
            print(f"\n=== {tbl.upper()} SCHEMA ===")
            if not cols:
                print("  (table does not exist)")
                continue
            for c in cols:
                print(f"  {c['column_name']:25s} {c['data_type']:20s} nullable={c['is_nullable']}")

            try:
                row = await pg_conn.fetchrow(f"SELECT * FROM {tbl} LIMIT 1")
                print("\n  First row:")
                print(f"  {dict(row)}" if row else "  (empty)")
            except Exception:
                print("  (table empty or inaccessible)")

        # Foreign keys
        fks = await pg_conn.fetch("""
            SELECT tc.table_name, kcu.column_name,
                   ccu.table_name AS foreign_table, ccu.column_name AS foreign_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name = ccu.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY'
            ORDER BY tc.table_name
        """)
        if fks:
            print("\n=== FOREIGN KEYS ===")
            for fk in fks:
                print(
                    f"  {fk['table_name']}.{fk['column_name']} -> {fk['foreign_table']}.{fk['foreign_column']}"
                )
        else:
            print("\n=== FOREIGN KEYS ===\n  (none)")

        # Indexes
        idxs = await pg_conn.fetch("""
            SELECT tablename, indexname FROM pg_indexes
            WHERE schemaname = 'public' ORDER BY tablename, indexname
        """)
        if idxs:
            print("\n=== INDEXES ===")
            for ix in idxs:
                print(f"  {ix['tablename']:20s} {ix['indexname']}")
        else:
            print("\n=== INDEXES ===\n  (none)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    print("=" * 60)
    print("KESTRAL E-COMMERCE - LOCAL POSTGRESQL SEEDER")
    print("=" * 60)

    start_postgres()
    print(f"Database: {POSTGRES_DB} (user: {POSTGRES_USER})")
    print(f"Host:     localhost:{HOST_PORT}")
    print(f"Data:     {DATA_DIR}\n")

    engine = create_async_engine(
        DB_URL,
        echo=False,
        pool_size=5,
        max_overflow=2,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        print("Database connection successful.\n")
    except Exception as e:
        print(f"[ERROR] Connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    await seed(engine, session_factory)
    await engine.dispose()
    print("\nKestral database ready. Container keeps running.\n")
    print(f"  Stop:    docker stop {CONTAINER_NAME}")
    print(f"  Start:   docker start {CONTAINER_NAME}")
    print(f"  Remove:  docker rm -f {CONTAINER_NAME}")
    print(f"  Nuke:    docker rm -f {CONTAINER_NAME} && rm -rf {DATA_DIR}")
    print("  Connect: docker exec -it kestral-postgres psql -U agentops -d kestral")


if __name__ == "__main__":
    asyncio.run(main())
