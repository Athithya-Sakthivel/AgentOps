# AgentOps — Executive Summary

**AI customer support agent for Kestral (fictional e-commerce). Real‑time WebSocket chat. LangGraph agent with DSPy‑optimized classification. 9 MCP tools. Multi‑turn memory. $23/month infrastructure.**

---

## Architecture

```
Internet
    │
    ▼
Cloudflare (Tunnel, WAF, DDoS, DNS, Rate Limiting)
    │
    ▼
2 × t4g.small ECS Managed Instances (multi‑AZ, public subnets, bridge networking)
    │
    ├── cloudflared (systemd service on host – not containerized)
    ├── agent‑service (ECS task, port 8000) — LangGraph + DSPy + inline policy search
    └── mcp‑server (ECS task, port 8001) — 9 MCP tools
    │
    ├── RDS PostgreSQL (private subnets) — business data, checkpoints, seed data
    ├── S3 — policy embeddings (1.3 MB JSON)
    ├── DynamoDB — rate limiting counters
    └── Bedrock — Llama 3 8B (LLM) + Titan Embeddings v2
```

---

## Key Architectural Decisions

| Decision | Implementation | Why |
|----------|----------------|-----|
| **ECS Managed Instances** | 2 × t4g.small, fixed capacity, multi‑AZ, bridge networking | No Kubernetes control plane ($73/mo) or per‑task Fargate premium ($142 → $20/mo). |
| **No NAT Gateway** | ECS runs in **public subnets** with Internet Gateway for outbound (ECR, CloudWatch). | Saves ~$32/mo. RDS stays in private subnets; inbound via Cloudflare Tunnel only. |
| **Cloudflare Tunnel as systemd service** | `cloudflared` installed on each EC2 host, managed by systemd, not as a container. | Zero inbound ports → no ALB ($22/mo) or AWS WAF ($8/mo). Free Cloudflare WAF/DDoS. |
| **RDS in private subnets** | PostgreSQL accessed only from ECS tasks (same VPC, security group rules). | Database never exposed to internet; no VPN or bastion required. |
| **S3 + brute‑force vector search** | Embeddings as 1.3 MB JSON; in‑memory cosine similarity (NumPy). | Eliminates vector database (OpenSearch/Qdrant ~$40‑190/mo). Latency <5ms for ≤400 chunks. |
| **Structured logs + correlation ID** | JSON logs with `run_id`; CloudWatch Logs + metrics; no X‑Ray/OTEL. | Sufficient for 2‑service debugging; saves ~$5‑15/mo and operational complexity. |
| **DynamoDB on‑demand** | Rate limiting counters. | No provisioning, pay only for requests (~$0/month at low volume). |

---

## Cost Breakdown

| Category | Over‑provisioned baseline | Optimized (this system) |
|----------|---------------------------|--------------------------|
| Compute | $142 (Fargate) | $20 (2 × t4g.small, fixed) |
| Networking + Security | $62 (ALB + NAT + AWS WAF) | $0 (Cloudflare Tunnel + free WAF) |
| Vector Search | $190 (OpenSearch + Qdrant) | ~$0.03 (S3 + Bedrock embeddings) |
| Observability | $15‑20 (verbose + X‑Ray) | ~$3 (structured logs, 7‑day retention) |
| **Total** | **~$414/month** | **~$23/month** |

**91% reduction. No added complexity; fewer moving parts.**

---

## Agent Capabilities (9 MCP Tools)

| Capability | Implementation |
|------------|----------------|
| Intent classification | DSPy‑compiled triage (Bedrock Llama 3 8B) |
| Customer lookup | MCP tool → PostgreSQL |
| Order history (last 5) | MCP tool → PostgreSQL |
| Policy Q&A | S3 + Bedrock Titan + NumPy (inline RAG, <5ms) |
| Refund eligibility | MCP tool → PostgreSQL (return window, billing checks) |
| Wallet credit (≤ Rs.500) | MCP tool → PostgreSQL with limit enforcement |
| Return pickup scheduling | MCP tool → PostgreSQL (eligibility guard) |
| Ticket creation | MCP tool → PostgreSQL (with automatic team routing) |
| Escalation | Automatic priority + assignment based on query severity |
| Multi‑turn memory | PostgreSQL checkpoints via LangGraph |
| Hallucination prevention | Order ID validation, tool‑result grounding |

---

## Infrastructure Highlights

- **No load balancer** – Cloudflare Tunnel routes HTTPS/WebSocket directly to ECS instances (port mapping 8000/8001).
- **No NAT gateway** – ECS tasks in public subnets use Internet Gateway for outbound (ECR pulls, CloudWatch logs).
- **RDS in private subnets** – Only accessible from ECS security group; no public IP.
- **Bridge networking** – avoids ENI limits on t4g.small; tasks share host network.
- **Systemd‑managed `cloudflared`** – independent of container lifecycle; starts before ECS agent.
- **Fixed capacity ASG** – `desired = min = max = 2`, `protect_from_scale_in = true` (required for ECS managed termination protection). Self‑healing if an instance fails.

This architecture is **production‑ready, cost‑optimised, and purposely minimal**.