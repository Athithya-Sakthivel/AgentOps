# AgentOps — Executive Summary

**AI customer support agent for Kestral (fictional e-commerce). Real-time WebSocket chat. LangGraph agent with DSPy-optimized classification. 9 MCP tools. Multi-turn memory. $23/month infrastructure.**

---

## Architecture

```
Cloudflare (Tunnel, WAF, DDoS, DNS, Rate Limiting)
    │
    ▼
2 × t4g.small ECS Managed Instances (multi‑AZ, bridge networking)
    │
    ├── cloudflared (systemd, not containerized)
    ├── agent‑service (ECS) — LangGraph + DSPy + inline policy search
    └── mcp‑server (ECS) — 9 MCP tools → PostgreSQL
    │
    ├── RDS PostgreSQL — business data, checkpoints, seed data
    ├── S3 — policy embeddings (1.3 MB JSON)
    └── Bedrock — Llama 3 8B (LLM) + Titan Embeddings v2
```

---

## Key Decisions

| ADR | Decision | Avoids | Saves/mo |
|-----|----------|--------|----------|
| 001 | ECS Managed Instances over EKS/Fargate | K8s overhead, per‑task pricing | ~$122 |
| 002 | Cloudflare Tunnel over ALB/NAT/WAF | ALB, NAT Gateway, WAF ACL | ~$62 |
| 003 | Structured logs + run_id over X‑Ray/OTEL | Tracing infra, collector overhead | ~$5‑15 |
| 004 | S3 + brute‑force over vector database | Qdrant, OpenSearch, pgvector | ~$190 |

---

## Cost

| Category | Over‑provisioned | Optimized |
|----------|------------------|-----------|
| Compute | $142 (Fargate) | $20 (2 × t4g.small) |
| Networking | $62 (ALB+NAT+WAF) | $0 (Cloudflare) |
| Vector Search | $190 (OpenSearch+Qdrant) | ~$0.03 (S3+Bedrock) |
| Observability | $15‑20 (verbose+X‑Ray) | ~$3 (structured logs) |
| **Total** | **~$414/month** | **~$23/month** |

**91% reduction. No complexity added.**

---

## Agent Capabilities

| Capability | How |
|------------|-----|
| Intent classification | DSPy‑compiled triage (Bedrock Llama 3 8B) |
| Customer lookup | MCP tool → PostgreSQL |
| Order history | MCP tool → PostgreSQL (last 5) |
| Policy Q&A | S3 + Bedrock Titan + NumPy (inline RAG, <5ms) |
| Refund eligibility | MCP tool → PostgreSQL (return window, billing checks) |
| Wallet credit | MCP tool → PostgreSQL (max Rs.500) |
| Return pickup | MCP tool → PostgreSQL (eligibility guard) |
| Ticket creation | MCP tool → PostgreSQL (with team routing) |
| Escalation | Automatic priority + team assignment |
| Multi‑turn memory | PostgreSQL checkpoints via LangGraph |
| Hallucination prevention | Order ID validation, tool‑result grounding |

---

## What's Not Needed

| Removed | Why |
|---------|-----|
| EKS | $73 control plane, operational overhead for 2 services |
| Vector database | 59 chunks → brute‑force <5ms |
| NAT Gateway | Cloudflare Tunnel + IGW for outbound |
| ALB | Cloudflare Tunnel handles ingress |
| X‑Ray / OTEL | run_id correlation sufficient at 2‑service scale |
| SNS / SQS / Step Functions | No async workloads |
| DynamoDB | Rate limiting at edge (Cloudflare), state in PostgreSQL |
| LangChain MCP adapters | Replaced by FastMCP Client SDK |