
# AgentOps Infrastructure Documentation

## 1. Overview

AgentOps is a production‑ready AI customer support agent that runs entirely on AWS with Cloudflare as the secure ingress layer. The system consists of two containerised services – `agent-service` (LangGraph + DSPy) and `mcp-server` (MCP tool server) – running on ECS Managed Instances. Traffic reaches the services via a Cloudflare Tunnel that terminates inside the VPC, eliminating the need for load balancers or NAT gateways.

The infrastructure is defined as code using OpenTofu (formerly Terraform) and is split into two independent stacks:

- **`src/infra/aws`** – all AWS resources (VPC, ECS, IAM, S3, DynamoDB, RDS, CloudWatch, Budgets)
- **`src/infra/cloudflare`** – Cloudflare Tunnel, DNS records, zone security settings

Both stacks are designed to be idempotent, secure, and cost‑optimised for a small‑scale production workload.

---

## 2. High‑Level Architecture

```
       ┌─────────────────────────────────────────────────────────────────┐
       │                         Cloudflare                               │
       │  ┌─────────────────────────────────────────────────────────────┐ │
       │  │   Zone: athithya.site                                      │ │
       │  │   - DNS CNAME: athithya.site → <tunnel-id>.cfargotunnel.com│ │
       │  │   - Wildcard CNAME: *.athithya.site → same tunnel           │ │
       │  │   - SSL: strict, Always HTTPS, TLS 1.3                      │ │
       │  │   - Bot Management: JS detections, crawler protection       │ │
       │  └─────────────────────────────────────────────────────────────┘ │
       └────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
                         Cloudflare Tunnel (cloudflared)
                                    │
                                    │ (encrypted, port 443)
                                    ▼
       ┌─────────────────────────────────────────────────────────────────┐
       │                     AWS (ap‑south‑1)                             │
       │  ┌─────────────────────────────────────────────────────────────┐│
       │  │ VPC: 10.20.0.0/16 (staging)                                 ││
       │  │  - Public subnets (2 AZs) with Internet Gateway             ││
       │  │  - Private subnets (2 AZs) for RDS (optional)               ││
       │  │                                                              ││
       │  │ EC2 Auto Scaling Group (ECS Managed Instances)              ││
       │  │  - 2 × t4g.small (ARM64)                                    ││
       │  │  - ECS-optimised Amazon Linux 2 ARM64 AMI                   ││
       │  │  - User‑data installs cloudflared and configures ECS agent  ││
       │  │                                                              ││
       │  │ ECS Cluster "agentops-staging-cluster"                      ││
       │  │  - Capacity provider linked to ASG                          ││
       │  │  - Container insights enabled                               ││
       │  │                                                              ││
       │  │ ECS Services (bridge networking)                            ││
       │  │  - agent-service: port 8000, needs task role (AWS APIs)     ││
       │  │  - mcp-server: port 8001, no task role (only PostgreSQL)    ││
       │  │                                                              ││
       │  │ Supporting services:                                        ││
       │  │  - S3 bucket: policy embeddings (JSON)                      ││
       │  │  - DynamoDB table: rate‑limiting counters (on‑demand)       ││
       │  │  - RDS (PostgreSQL, optional): db.t4g.micro, no Secrets Mgr ││
       │  │  - CloudWatch log groups, metric filters, dashboard, alarms ││
       │  │  - AWS Budget: monthly cost alerts (80%, 100%)              ││
       │  └─────────────────────────────────────────────────────────────┘│
       └─────────────────────────────────────────────────────────────────┘
```

All communication between the Cloudflare Tunnel and the agent‑service happens over the host’s loopback interface (`localhost:8000`). The tunnel terminates on each EC2 instance, so no load balancer or public IP is required.

---

## 3. AWS Infrastructure Stack (`src/infra/aws`)

### 3.1 Core Networking (Module `vpc`)

- **VPC** with DNS support and hostnames enabled.
- **Internet Gateway** attached to the VPC.
- **Public subnets** (one per AZ) with `map_public_ip_on_launch = true` and a route to the IGW.
- **Private subnets** (optional) for RDS; they have no direct internet access.
- Outputs: VPC ID, public/private subnet IDs, public route table ID.

### 3.2 Security Groups (Module `security-groups`)

- `ecs-sg`: allows all outbound traffic (needed for ECR, CloudWatch). No inbound rules by default; Cloudflare Tunnel makes inbound public access unnecessary.
- `rds-sg`: allows inbound PostgreSQL (5432) only from the `ecs-sg`.
- Optional: SSH inbound from a specific CIDR block for debugging (disabled in production).

### 3.3 Object Storage (Module `s3`)

- One S3 bucket per environment (e.g., `agentops-staging-embeddings-bucket`) storing the pre‑computed embeddings JSON file.
- **Public access block**: all four flags set to `true`.
- **Versioning** enabled.
- **Force destroy** configurable – `true` for staging, `false` for production.

### 3.4 NoSQL Database (Module `dynamodb`)

- Table: `agentops-<env>-rate-limits`
- Primary key: `pk` (partition), `sk` (sort) – both strings.
- Billing mode: `PAY_PER_REQUEST` (on‑demand, no provisioned capacity).
- TTL enabled on attribute `ttl` for automatic cleanup of expired rate‑limit counters.
- Point‑in‑time recovery enabled only for production.
- Server‑side encryption enabled.

### 3.5 Container Registry (Module `ecr`)

- Two repositories per environment:
  - `agentops-<env>-agent-service`
  - `agentops-<env>-mcp-server`
- **Immutable tags** (`image_tag_mutability = "IMMUTABLE"`).
- Vulnerability scanning on push.
- Lifecycle policy: keep the last 50 images, expire untagged images older than 14 days.
- Force delete configurable (staging = true, prod = false).

### 3.6 IAM (Module `iam`)

The IAM module is called twice from the root – once for ECS roles, once for GitHub OIDC roles. Both share the same source but use a flag `create_ecs_roles` to conditionally create the ECS‑specific resources.

#### ECS Roles (`create_ecs_roles = true`)

- **Instance profile role** (`ecs-instance-role`): allows the EC2 instances to register with the ECS cluster. Attached policy `AmazonEC2ContainerServiceforEC2Role`.
- **Task execution role** (`ecs-task-execution-role`): allows ECS tasks to pull images from ECR and write logs to CloudWatch. Attached managed policy `AmazonECSTaskExecutionRolePolicy`.
- **Task role for agent‑service** (`agent-task-role`): grants permissions for:
  - SSM GetParameter (read secrets)
  - KMS Decrypt (for SecureStrings)
  - Bedrock InvokeModel / InvokeModelWithResponseStream / ListFoundationModels
  - S3 GetObject / ListBucket (policy embeddings)
  - DynamoDB UpdateItem / GetItem (rate limiting)
  - CloudWatch Logs and Metrics (`*`)
  - ECR BatchGetImage etc. (already covered by execution role, but included for completeness)

The `mcp-server` **does not** have a task role – it connects only to PostgreSQL via TCP and needs no AWS API permissions.

#### GitHub OIDC Roles (`create_ecs_roles = false`)

- One IAM role per ECR repository: `gh-actions-agentops-agent` and `gh-actions-agentops-mcp`.
- Trust policy allows `sts:AssumeRoleWithWebIdentity` from GitHub (repo: `Athithya-Sakthivel/AgentOps`, branch: `main`).
- Permission policy allows full ECR push/pull (`GetAuthorizationToken`, `BatchCheckLayerAvailability`, `PutImage`, etc.) for the corresponding repository.
- Used by GitHub Actions to build and push Docker images.

### 3.7 Relational Database (Module `rds`, optional)

- Created only if `create_rds = true` (production). In staging it is disabled to save cost.
- PostgreSQL 16.4, `db.t4g.micro` (free tier eligible).
- Master password: either provided via `db_password` or generated randomly (16 chars, no special characters).
- **No Secrets Manager** – the password is passed directly to the ECS task definition as an environment variable (`DATABASE_URL`). The connection string is constructed in the RDS module output and marked `sensitive`.
- Private subnets, security group restricts access to the ECS security group.
- Backups: 7 days retention, maintenance window set.
- Deletion protection enabled for production, disabled for staging.
- Performance Insights only for production.

### 3.8 Observability (Module `observability`)

- Two CloudWatch log groups:
  - `/ecs/<name_prefix>-agent-service`
  - `/ecs/<name_prefix>-mcp-server`
- Retention: 7 days for staging, 30 days for production.
- Metric filters (extracted from structured JSON logs):
  - `AgentRequests` – message “Message processed”
  - `WalletCredits` – message “Wallet credit issued”
  - `TicketsCreated` – message “Ticket created”
  - `Errors` – field `level = "ERROR"`
- CloudWatch dashboard (free tier) with three widgets: agent activity (metrics), error count, recent errors log table.
- Two alarms (conditional on `alarm_sns_topic_arn` being non‑empty):
  - `errors-high`: >5 errors in 5 minutes
  - `no-requests`: no agent requests for 30 minutes

### 3.9 Cost Budget (Module `budget`)

- Monthly AWS budget (cost type, USD).
- Two notifications: 80% of budget (actual) and 100% forecasted.
- Emails sent to the provided list.
- Budget amount configurable per environment (e.g., $100 staging, $500 production).

### 3.10 ECS Cluster (Module `ecs-cluster`)

- **Launch template**:
  - Uses the latest ECS‑optimised Amazon Linux 2 **ARM64** AMI (matched with `t4g.small` instance type).
  - User‑data script (see Section 5) passes the Cloudflare tunnel token and hostname.
- **Auto Scaling Group**:
  - Fixed capacity: `min = max = desired = 2`, one instance per AZ.
  - `protect_from_scale_in = true` to satisfy ECS managed termination protection.
  - Mandatory tag `AmazonECSManaged = true` propagated at launch.
- **ECS cluster** with container insights enabled.
- **Capacity provider** linked to the ASG, with managed scaling (target capacity 100%) and managed termination protection enabled.
- The cluster is then associated with the capacity provider using `aws_ecs_cluster_capacity_providers`.

### 3.11 ECS Services (Module `ecs-services`)

- Two task definitions (bridge networking, EC2 launch type):
  - `agent` – CPU 512, memory 1024, execution role + task role, environment includes `DATABASE_URL` (if RDS created) and `ENVIRONMENT`.
  - `mcp` – CPU 256, memory 512, execution role only (no task role).
- Two ECS services, each with desired count 2, spread placement constraint (`distinctInstance`), using the capacity provider.
- No `network_configuration` block – bridge networking uses the host’s network, avoiding ENI limits.

### 3.12 Root Variables and Environment

All variables are passed exclusively via `TF_VAR_*` environment variables. The wrapper script `run.sh` (inside `src/infra/aws`) sources these from the shell environment, providing defaults in the script itself. There are no `.tfvars` files.

Key variables include:

| Variable | Description | Staging default | Prod typical |
|----------|-------------|----------------|---------------|
| `TF_VAR_region` | AWS region | `ap-south-1` | `ap-south-1` |
| `TF_VAR_environment` | Environment name | `staging` | `prod` |
| `TF_VAR_name_prefix` | Resource prefix | `agentops-staging` | `agentops-prod` |
| `TF_VAR_vpc_cidr_block` | VPC CIDR | `10.20.0.0/16` | `10.30.0.0/16` |
| `TF_VAR_public_subnet_cidrs` | List of CIDRs | `["10.20.1.0/24","10.20.2.0/24"]` | similar with 10.30.x |
| `TF_VAR_private_subnet_cidrs` | List for RDS | `["10.20.11.0/24","10.20.12.0/24"]` | similar |
| `TF_VAR_force_destroy` | Allow deletion of non‑empty resources | `true` | `false` |
| `TF_VAR_github_repository` | GitHub repo (owner/name) | `Athithya-Sakthivel/AgentOps` | same |
| `TF_VAR_cloudflare_tunnel_token` | Tunnel token (sensitive) | obtained from Cloudflare stack | obtained from Cloudflare stack |
| `TF_VAR_create_rds` | Create RDS instance | `false` | `true` |
| `TF_VAR_db_password` | RDS master password (optional) | empty | empty (generates random) |
| `TF_VAR_monthly_budget_amount` | Monthly cost limit (USD) | `100` | `500` |
| `TF_VAR_alert_emails` | Emails for budget alerts | `["alerts+staging@agentops.com"]` | `["alerts@agentops.com","finance@agentops.com"]` |
| `TF_VAR_enable_ecs` | Deploy ECS resources | `true` | `true` |

The script `run.sh` provides all these with sensible defaults for staging; for production, set the overrides before calling the script.

---

## 4. Cloudflare Stack (`src/infra/cloudflare`)

### 4.1 Resources Created

- **Zero Trust Tunnel**: named (default `agentops-tunnel`) – created or reused.
- **DNS Records**:
  - `athithya.site` CNAME → `<tunnel-id>.cfargotunnel.com` (proxied).
  - `*.athithya.site` CNAME → same tunnel (proxied).
- **Zone Settings**:
  - SSL mode: `strict`
  - Always use HTTPS: `on`
  - TLS 1.3: `on`
- **Bot Management**:
  - Fight mode: `false` (disabled)
  - JS detections: `true`
  - AI bots protection: `block`
  - Crawler protection: `enabled`

### 4.2 Outputs

- `cloudflare_tunnel_id`
- `cloudflare_tunnel_name`
- `cloudflare_tunnel_token` (sensitive)
- `root_url` = `https://athithya.site`

### 4.3 Authentication

The stack supports two authentication methods:
- **API Token** (recommended) – set `CLOUDFLARE_API_TOKEN`.
- **Global API Key + Email** – set `CLOUDFLARE_GLOBAL_API_KEY` and `CLOUDFLARE_EMAIL`.

### 4.4 Runtime Script (`run.sh`)

The script performs several critical tasks:

1. Resolves the zone ID from the domain.
2. Logs in to `cloudflared` if needed.
3. Ensures the named tunnel exists; retrieves its ID and token.
4. Imports existing DNS records and zone settings into the OpenTofu state (idempotency).
5. Runs `init`, `validate`, then `plan` / `apply` / `destroy` as requested.
6. On destroy, deletes the tunnel (`cloudflared tunnel delete -f`).

The tunnel token is printed after an apply and can be used to set `TF_VAR_cloudflare_tunnel_token` for the AWS stack.

---

## 5. Integration: Cloudflare Tunnel + ECS

The glue between Cloudflare and AWS is the EC2 user‑data script (`modules/ecs-cluster/user_data.sh`). It runs once per instance at boot:

1. Downloads the `cloudflared` binary (ARM64 version) from GitHub.
2. Installs `cloudflared` as a systemd service using the token passed from the launch template.
3. Writes the ingress configuration to `/etc/cloudflared/config.yml` (the tunnel routes all traffic on `athithya.site` to `http://localhost:8000`).
4. Restarts the `cloudflared` service.
5. Configures the ECS agent by writing `/etc/ecs/ecs.config` with:
   ```
   ECS_CLUSTER=<cluster-name>
   ECS_ENABLE_MANAGED_INSTANCE=true
   ```
6. Starts the ECS agent (`systemctl enable --now ecs`).

The launch template passes three variables to the templatefile: `cluster_name`, `cloudflare_tunnel_token`, and `cloudflare_hostname`. The tunnel token is obtained from the Cloudflare stack output.

Because the tunnel is installed directly on each EC2 instance, traffic can reach the `agent-service` container (listening on port 8000) even though the container uses bridge networking (dynamic host port). The `cloudflared` service connects to `localhost:8000` on the same host, and the ECS agent maps the container’s port 8000 to an ephemeral host port. This works because `cloudflared` and the container share the host’s network namespace.

---

## 6. Deployment Workflow

### 6.1 Prerequisites

- AWS credentials configured (environment variables or IAM instance role).
- OpenTofu (`tofu`) ≥1.9 installed.
- `jq`, `curl`, `cloudflared` CLI installed (for the Cloudflare stack).
- Cloudflare API token or global key with permissions for DNS, Zero Trust, and zone settings.

### 6.2 Deploy Cloudflare Stack (first)

```bash
cd src/infra/cloudflare
export CLOUDFLARE_ACCOUNT_ID="your-account-id"
export CLOUDFLARE_API_TOKEN="your-api-token"
export TF_VAR_domain="athithya.site"
bash run.sh --apply
```

Capture the `cloudflare_tunnel_token` output:

```bash
export TF_VAR_cloudflare_tunnel_token="$(tofu output -raw cloudflare_tunnel_token)"
```

### 6.3 Deploy AWS Stack

Set the required environment variables (see Section 3.12). A helper script `run.sh` inside `src/infra/aws` can source a predefined set (e.g., `exports-staging.sh`) but is not required – all variables must be in the environment.

```bash
cd src/infra/aws
export TF_VAR_environment=staging
export TF_VAR_cloudflare_tunnel_token="$TF_VAR_cloudflare_tunnel_token"
# … other variables
bash run.sh --create --env staging
```

The AWS `run.sh` script:
- Creates the S3 state bucket if it does not exist (versioning + encryption).
- Initialises the backend.
- Runs `tofu fmt`, `validate`, `plan`, and `apply` (or `destroy`).
- For `--destroy`, it also calls a `force_cleanup` function that manually dismantles ECS resources, disassociates the capacity provider, deletes the ASG, launch template, and Internet Gateway to avoid stuck dependencies.

### 6.4 Destroy

```bash
cd src/infra/aws
bash run.sh --destroy --env staging --yes-delete
```

Then destroy the Cloudflare stack:

```bash
cd src/infra/cloudflare
bash run.sh --destroy
```

---

## 7. Key Architectural Decisions (from ADRs)

| ADR | Decision | Rationale |
|-----|----------|-----------|
| 001 | **ECS Managed Instances (2× t4g.small) over EKS/Fargate** | Eliminates Kubernetes control plane cost ($73) and operational burden. Fargate has a 14× cost premium for steady‑state workloads. |
| 002 | **Cloudflare Tunnel over ALB + NAT + WAF** | Zero networking cost, zero inbound attack surface, managed WAF at edge. |
| 003 | **Structured logs + correlation IDs over distributed tracing** | Simpler, sufficient for 2‑service system. Metrics extracted via CloudWatch metric filters. |
| 004 | **S3 + brute‑force search over vector database** | At ~400 chunks, in‑process cosine similarity is <5ms. Eliminates vector DB cost (~$40–150) and complexity. |

---

## 8. Cost Overview (Monthly, Fixed)

| Resource | Staging | Production |
|----------|---------|------------|
| ECS Managed Instances (2 × t4g.small, Reserved) | $20 | $20 |
| S3 (embeddings + logs) | ~$0.03 | ~$0.03 |
| DynamoDB (on‑demand, idle) | ~$0 | ~$0 |
| RDS (db.t4g.micro) | – | $15 (on‑demand) |
| CloudWatch (logs, metrics, dashboard) | ~$0 (free tier) | ~$0 (within free tier) |
| AWS Budget | $0 | $0 |
| Cloudflare Tunnel | $0 (free plan) | $0 |
| **Total** | **~$23** | **~$38** |

These costs do not include Bedrock LLM usage or data transfer (negligible). The architecture achieves a **91% reduction** compared to a typical Fargate+ALB+NAT+OpenSearch baseline.

---

## 9. Security & Compliance

- **Network**:
  - No inbound ports open on EC2 security groups.
  - RDS in private subnets, accessible only from ECS security group.
  - Cloudflare Tunnel provides mutual TLS and encrypted ingress.
- **IAM**:
  - Least‑privilege policies: ECS execution role only has ECR and logs; agent task role has only the specific actions required.
  - GitHub OIDC roles are scoped to single ECR repositories.
- **Secrets**:
  - RDS password passed as an environment variable (acceptable for initial rollout; future iteration could use Secrets Manager with ephemeral password).
  - Cloudflare tunnel token is stored only in the EC2 user‑data (which is not retrievable after launch) and in the OpenTofu state (which is encrypted in S3).
- **Encryption**:
  - S3 bucket encrypted with AES‑256.
  - DynamoDB encrypted at rest by default.
  - RDS storage encrypted.
  - State bucket encrypted and versioned.
- **Observability**:
  - CloudWatch alarms on errors and service silence.
  - Budget alerts at 80% and 100% of monthly spend.

---

## 10. Troubleshooting & Common Issues

### 10.1 Cloudflare Tunnel returns 502 / 1033

- **502** → tunnel is reachable but no service listening on port 8000. Check that the `agent-service` ECS task is running (`tofu output agent_service_name`, then `aws ecs list-tasks`).
- **1033** → tunnel is completely down. Verify `cloudflared` service status on the EC2 instances (`systemctl status cloudflared`). Re‑run the user‑data or check that the tunnel token is valid.

### 10.2 ECS tasks stuck in `PROVISIONING` or `PENDING`

- Ensure the Auto Scaling Group has healthy instances (`aws ec2 describe-instances`).
- Verify the instance profile has the `AmazonEC2ContainerServiceforEC2Role` policy.
- Check that the ECS agent is running (`systemctl status ecs` on an instance).

### 10.3 Destroy gets stuck on ECS services or IGW

The `run.sh` script’s `force_cleanup` function handles this automatically. If you encounter a stale lock, use `tofu force-unlock <lock-id>`.

### 10.4 Deprecation warnings about `data.aws_region.current.name`

The code has been updated to use `region` instead of `name`. All modules reflect this change.

---

## 11. Repository Layout

```
src/infra/
├── aws/
│   ├── modules/
│   │   ├── budget/
│   │   ├── dynamodb/
│   │   ├── ecr/
│   │   ├── ecs-cluster/
│   │   ├── ecs-services/
│   │   ├── iam/
│   │   ├── observability/
│   │   ├── rds/
│   │   ├── s3/
│   │   ├── security-groups/
│   │   └── vpc/
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── providers.tf
│   ├── backend.tf
│   ├── run.sh
│   └── .plans/
└── cloudflare/
    ├── main.tf
    ├── variables.tf
    ├── outputs.tf
    ├── run.sh
    ├── README.md
    └── tfplan
```

All modules are self‑contained, and the root configuration passes variables via `TF_VAR_*` exclusively.

---

## 12. Future Improvements

- **Secrets Manager for RDS password** with automatic rotation (requires Lambda).
- **Enable cloudflare bot/js protection** currently disabled

---