# ADR 001: ECS Managed Instances over ECS Fargate and EKS

## Context
The system runs two persistent services (agent-service, mcp-server) that
must be available 24/7 for WebSocket chat connections. Both maintain
in-memory state (LangGraph agent graph, PostgreSQL connection pool,
cached policy embeddings). The workload is steady-state with fixed
replicas—no burst scaling, no idle periods.

A previous project used EKS for a RAG system. That experience surfaced
the operational burden of Kubernetes for small teams: control plane cost
($73/month), node patching, etcd backups, CNI plugin management, and
YAML sprawl across Deployments, Services, Ingresses, ConfigMaps, and
Secrets. For a 2-service system, this overhead is unjustifiable.

## Decision
Use two ECS Managed Instances (t4g.small, 2 vCPU/2 GB each, ~$20/month
Reserved total) across two Availability Zones. Each instance runs both
services in bridge networking mode. ECS spreads tasks across instances
for availability.

## Options Considered

| Option | Monthly Cost | Trade-off |
|--------|-------------|-----------|
| EKS (managed Kubernetes) | ~$83 + compute | Full Kubernetes API, portable workloads. $73 control plane + node management overhead for 2 services. |
| ECS Fargate (4 tasks) | ~$142 | Zero capacity management, 14x cost premium for steady-state workload |
| ECS Managed Instances (2 × t4g.small, multi-AZ) | ~$20 | EC2 pricing, AWS handles OS patching/AMI lifecycle. Multi-AZ availability. |
| ECS Managed Instances (1 × t4g.small, single-AZ) | ~$10 | Half the cost, no AZ redundancy |

## Rationale
- EKS adds $73/month control plane cost before any compute. For 2 services,
  this exceeds the entire infrastructure budget of this system.
- EKS operational overhead (cluster upgrades, node patching, CNI management,
  etcd backups) provides no value at this scale. The previous project
  confirmed this firsthand.
- Steady-state workload doesn't benefit from Fargate's scaling premium.
- Managed Instances handle OS patching, AMI updates, and unhealthy
  instance replacement—equivalent to Fargate for the OS layer without
  the per-task pricing.
- Bridge networking avoids ENI limits entirely. t4g.small has 2 ENI slots
  available for ECS tasks in awsvpc mode. Bridge mode uses 0—containers
  share the host network interface via port mapping.
- Two instances across AZs provide availability: if one AZ fails or an
  instance becomes unhealthy, the remaining instance continues serving
  traffic while ECS launches a replacement.

## Consequences
- **Positive:** 76% reduction in compute cost vs EKS ($83 → $20/month).
  Multi-AZ availability without Kubernetes. No Kubernetes to maintain.
  Same container images, simpler deployment model.
- **Negative:** Must configure ASG, capacity provider, and launch template.
  Two instances instead of zero-management Fargate (acceptable trade-off).
  Not portable to other cloud providers (acceptable—AWS-native architecture).

## When to Revisit
- Service count grows beyond 5-6 (ECS service definitions become unwieldy).
- Multi-cloud portability becomes a requirement.
- Team already has Kubernetes expertise and operational tooling.
- Zero capacity management becomes worth the Fargate premium.