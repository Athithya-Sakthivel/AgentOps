## AgentOps — Executive Summary

**An AI customer support agent for an e-commerce company (Kestral). Real-time chat via WebSocket with streaming LLM responses. Multi-step agent reasoning with LangGraph. MCP-standardized tool integration. DSPy-optimized prompts. Zero Kubernetes. $23/month infrastructure.**

---

### Architecture

```
Cloudflare (Tunnel, WAF, DDoS, DNS)
    │
    ▼
2 × t4g.small Managed Instances (multi-AZ)
    │
    ├── cloudflared (systemd, not containerized)
    ├── agent-service (ECS, bridge) — LangGraph + DSPy + policy search
    └── mcp-server (ECS, bridge) — 9 MCP tools → PostgreSQL
    │
    ├── RDS PostgreSQL (10 tables, checkpoints, seed data)
    ├── DynamoDB (rate limiting, TTL)
    ├── S3 (policy embeddings, 2.5 MB JSON)
    └── Bedrock (Claude 3 Sonnet + Titan Embeddings)
```

---

### 4 Key Architectural Decisions

| ADR | Decision | Avoids | Saves |
|-----|----------|--------|-------|
| **001** | ECS Managed Instances over EKS/Fargate | K8s overhead, per-task pricing | ~$122/mo |
| **002** | Cloudflare Tunnel over ALB/NAT/WAF | ALB hourly, NAT hourly, WAF ACL | ~$62/mo |
| **003** | Structured logs + correlation IDs over X-Ray/OTEL | Tracing infra, collector overhead | ~$5-15/mo |
| **004** | S3 + brute-force cosine similarity over vector DB | Qdrant, OpenSearch, pgvector | ~$190/mo |

---

### Cost Profile

| Category | Unoptimized Default | Optimized |
|----------|---------------------|-----------|
| Compute | $142 (Fargate) | $20 (2 × t4g.small) |
| Networking | $62 (ALB+NAT+WAF) | $0 (Cloudflare Tunnel) |
| Vector Search | $190 (OpenSearch+Qdrant) | ~$0.03 (S3+Bedrock) |
| Observability | $15-20 (verbose+X-Ray) | ~$3 (structured logs) |
| **Total** | **~$414/month** | **~$23/month** |

**91% cost reduction. Every optimization reduces or maintains complexity.**

---

### Workloads

| Type | What | Where |
|------|------|-------|
| Real-time chat | WebSocket streaming, LangGraph agent | agent-service (ECS, always warm) |
| Tool execution | 9 MCP tools (lookup, refund, ticket, etc.) | mcp-server (ECS, always warm) |
| Policy search | Query embedding → cosine similarity → top-K | agent-service (in-process, S3-cached) |
| Rate limiting | Atomic counters, per-connection | DynamoDB (on-demand) |
| Auth | WebSocket $connect validation | Lambda Authorizer |
| Checkpoints | LangGraph state persistence | PostgreSQL |

---

### What's NOT in the System

| Not Present | Why |
|-------------|-----|
| Kubernetes (EKS) | $73 control plane, operational overhead for 2 services |
| Vector database | 400 chunks → brute-force <5ms, no ANN needed |
| NAT Gateway | Public subnet + IGW for outbound (free) |
| Application Load Balancer | Cloudflare Tunnel handles all ingress |
| X-Ray / OTEL Collector | Correlation IDs + structured logs sufficient at 2-service scale |
| SNS / SQS / Step Functions | No async workloads justify them |
| Lambda (beyond authorizer) | No long-running background tasks |