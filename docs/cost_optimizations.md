## Cost Optimizations — Ranked by Impact

| # | Decision | Avoids | Savings/Month | Complexity Impact |
|---|----------|--------|---------------|-------------------|
| 1 | **ECS Managed Instances (2 × t4g.small)** over Fargate (4 tasks) | Per-task pricing premium for steady-state workload | ~$122 | Low (ASG + capacity provider) |
| 2 | **Cloudflare Tunnel + WAF** over ALB + NAT Gateway + AWS WAF | ALB hourly, NAT hourly, WAF Web ACL charges | ~$62 | Reduced (fewer resources) |
| 3 | **S3 + Bedrock brute-force retrieval** over OpenSearch Serverless + Qdrant | Managed vector database OCUs + dedicated instance | ~$190 | Reduced (eliminated 2 services) |
| 4 | **Structured logs + correlation IDs** over X-Ray distributed tracing | Per-trace charges, OTEL Collector overhead | ~$5-15 | Reduced (no tracing infra) |

**Non-overlapping total: ~$380/month eliminated. Every optimization either reduces or maintains complexity.**

---

## Total Fixed Infrastructure

| Category | Overprovisioned Default | Optimized |
|----------|------------------------|-----------|
| Compute | $142 (Fargate, 4 tasks) | $20 (2 × t4g.small Managed Instances, multi-AZ) |
| Networking + Security | $62 (ALB + NAT + AWS WAF) | $0 (Cloudflare Tunnel + WAF) |
| Vector Search | $190 (OpenSearch + Qdrant) | ~$0.03 (S3 + Bedrock embeddings) |
| Observability | $10-20 (verbose logs + X-Ray) | ~$3 (structured logs, 7-day retention) |
| **Total** | **~$414/month** | **~$23/month** |

**Portfolio scale: ~$23/month. Production with RDS always-on: ~$38/month — a 91% reduction from the equivalent Fargate+ALB+NAT+OpenSearch baseline.**