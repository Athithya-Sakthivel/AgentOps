# MCP Server — Ideal Specification (Rebuild Target)

## Overview

The MCP server exposes **three tools** over HTTP using FastMCP.  
It is the **data layer** for the agent.  It never contains business logic, validation, or decision‑making.  That stays in the agent‑service.

The three tools:

1. **`lookup_customer`** – find a customer by email or phone  
2. **`get_recent_orders`** – retrieve the 5 most recent orders (with product details)  
3. **`create_ticket`** – create a support ticket with a rich summary and a suggested action

All other capabilities (policy search, team routing, summarisation) live in the agent‑service.

---

## 1. `lookup_customer`

**Input**
```json
{
  "email": "priya.sharma@email.com"
}
```

**Output**
```json
{
  "id": "a1b2c3d4-e5f6-4a7b-8c9d-000000000001",
  "full_name": "Priya Sharma",
  "email": "priya.sharma@email.com",
  "phone": "+919876543210",
  "language_pref": "en",
  "segment": "premium",
  "created_at": "2025-01-15T10:30:00Z"
}
```

Returns `null` if no customer matches.

---

## 2. `get_recent_orders`

**Input**
```json
{
  "user_id": "a1b2c3d4-e5f6-4a7b-8c9d-000000000001"
}
```

**Output** (up to 5 orders, newest first)
```json
[
  {
    "id": "c3d4e5f6-...",
    "status": "delivered",
    "amount": "124999.00",
    "order_date": "2026-05-10T10:00:00Z",
    "delivery_date": "2026-05-14T14:00:00Z",
    "tracking_number": "KST-BLR-001",
    "product_name": "Samsung Galaxy S25 Ultra 5G",
    "return_window_days": 10,
    "is_returnable": true
  }
]
```

Returns an empty array if the customer has no orders.

---

## 3. `create_ticket`

This is the **only write operation** the agent ever performs.  
It accepts the same fields as the old tool, plus **two new fields** that make the ticket immediately useful to a human resolver.

**Input**
```json
{
  "user_id": "a1b2c3d4-...",
  "query_text": "I ordered a smartphone but got a charger.",
  "classification": {
    "intent": "wrong_item_delivered",
    "urgency": 7,
    "sentiment": "frustrated",
    "auto_resolvable": false
  },
  "priority": "high",
  "assigned_team": "order_fulfillment",
  "summary": "Customer received a charger instead of the Samsung Galaxy S25 Ultra (order #KST-BLR-9901). Needs immediate replacement and investigation.",
  "suggested_action": "Verify shipment, initiate return pickup for incorrect item, and ship correct phone with expedited delivery."
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `user_id` | Yes | UUID of the customer |
| `query_text` | Yes | Original message from the customer |
| `classification` | Yes | DSPy output (intent, urgency, etc.) |
| `priority` | Yes | `critical` / `high` / `medium` |
| `assigned_team` | Yes | Team that will work on the ticket |
| `summary` | Yes | 2‑3 sentence AI‑written summary of the issue |
| `suggested_action` | Yes | One‑line recommendation for the support agent |

**Output**
```json
{
  "ticket_id": "c933e52a-5415-4320-9bbe-6edbf677ae4a"
}
```

**Side effects**  
- Creates a ticket row with `status = 'pending_human'`, `resolution_type = 'escalated'`.  
- The `summary` and `suggested_action` are stored directly in the tickets table.  
- The agent‑service can later read these fields for the admin dashboard.

---

## Database changes (one‑time migration)

```sql
ALTER TABLE tickets
  ADD COLUMN IF NOT EXISTS summary TEXT,
  ADD COLUMN IF NOT EXISTS suggested_action TEXT;
```

---

## What we removed (and why)

| Removed tool | Reason |
|-------------|--------|
| `issue_wallet_credit` | Operational task – handled by billing systems, not an AI agent. |
| `schedule_return_pickup` | Operational task – handled by logistics systems. |
| `check_refund_eligibility` | Policy check now done by agent‑service via RAG, not by direct DB query. |
| `escalate_to_human` | Redundant – all tickets are already routed to the right team. |
| `route_to_team` | Team assignment is set at ticket creation time. |
| `get_order_details` | Not needed by the agent – recent orders already contain product info. The admin dashboard queries the database directly. |

The rebuilt MCP server will have **3 tools** instead of 9, making it simpler, faster, and easier to maintain.