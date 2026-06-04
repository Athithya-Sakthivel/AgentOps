# AgentOps

**AgentOps** is an AI‑powered ticket triage system for e‑commerce support teams.  It classifies customer messages, gathers context (profile, recent orders, policies), deterministically routes tickets to the correct team, and writes a human‑readable summary with a suggested action – so a support agent can resolve a ticket in seconds instead of minutes.  It never autonomously refunds or modifies orders; it purely **makes human agents faster**.  The stack is LangGraph, DSPy, FastAPI, Bedrock (Llama 3.1 8B), PostgreSQL, and MCP tools, all served through a Cloudflare Tunnel on AWS for about **$23/month**.


# Get started

## Prerequisites
1. **Docker installed, running *without* sudo access**
2. **Visual Studio Code with the Dev Containers extension installed (for a deterministic environments): [https://code.visualstudio.com/docs/devcontainers/containers](https://code.visualstudio.com/docs/devcontainers/containers)**
3. **An AWS account with sufficient IAM permissions (AdministratorAccess or equivalent) to manage**:
   * Amazon ECS (Elastic Container Service)
   * EC2, VPCs, Subnets, and Security Groups
   * Amazon S3
   * IAM Roles, Policies, and Instance Profiles
   **AWS Free Tier is sufficient for development and testing purposes.**
4. **A Cloudflare account with a registered domain, with permissions to manage DNS records and create Cloudflare Tunnels (cloudflared)**

## Clone the repo and build the devcontainer(Reproducible). This will take 10-20 minutes. 
```sh 
cd $HOME && rm -rf E2E-RAG-System && git clone https://github.com/Athithya-Sakthivel/AgentOps.git && cd AgentOps && code .
```
> ctrl + shift + P -> paste `Dev containers: Rebuild Container Without Cache` and enter

### Open a new terminal and login to your gh account
```sh
git config --global user.name "Your Name" && git config --global user.email you@example.com
gh auth login

? What account do you want to log into? GitHub.com
? What is your preferred protocol for Git operations? SSH
? Generate a new SSH key to add to your GitHub account? No
? How would you like to authenticate GitHub CLI? Login with a web browser

! First copy your one-time code: <code>
- Press Enter to open github.com in your browser... 
✓ Authentication complete. Press Enter to continue...
```

### Create a private repo in your gh account

```sh
export REPO_NAME="AgentOps" # or any name
git remote remove origin 2>/dev/null || true
gh repo create "$REPO_NAME" --private >/dev/null 2>&1
REMOTE_URL="https://github.com/$(gh api user | jq -r .login)/$REPO_NAME.git"
git remote add origin "$REMOTE_URL" 2>/dev/null || true
git branch -M main 2>/dev/null || true
git push -u origin main
git pull
git remote -v
echo "[INFO] A private repo '$REPO_NAME' created and pushed. Only visible from your account."
```
---

### Phase 1: Infrastructure Foundation

#### 1.1 Set Up Cloudflare Tunnel and DNS. [Docs](src/infra/cloudflare/README.md)
Creates DNS records and a Cloudflare Tunnel that securely routes traffic to your ECS cluster — no Load Balancers or public IPs needed. The script waits for you to authorize Cloudflare access. The script waits for you to login to your cloudflare account from your default browser.

```sh
export CLOUDFLARE_ACCOUNT_ID=
export CLOUDFLARE_GLOBAL_API_KEY=
export CLOUDFLARE_EMAIL="athithya651@gmail.com" # Replace with your email
export DOMAIN="athithya.site"  # replace with your domain
bash src/infra/cloudflare/run.sh --apply
```

<details>
<summary>▶ Expected output</summary>

![alt text](src/offline/images/cf.png)

</details>

#### 1.2 Provision AWS Infrastructure. [Docs](docs/infra.md)
Creates the VPC, ECS cluster, S3 bucket, ECR repositories, DynamoDB, RDS, and all IAM roles. Uses OpenTofu (Terraform-compatible).

```sh
export TF_VAR_region="ap-south-1"
export TF_VAR_github_repository="Athithya-Sakthivel/AgentOps"   # replace with your GitHub repo
bash src/infra/aws/run.sh --create --env staging
```

<details>
<summary>▶ Expected output</summary>

![alt text](src/offline/images/aws.png)

</details>


### Phase 2: Data Preparation(Mimic a fictional e-commerce company named kestral)
 - Seed the PostgreSQL Database with Mock Data [Docs](docs/pg_tables.md) 
 - Index markdown policy documents into S3 as a single embeddings JSON file [Docs](docs/serverless_rag.md)
 - After the infrastructure is up, you need to populate the RDS database with tables and mock data for the agent to function (e.g., customers, orders, support tickets). The command below automatically fetches your IP and adds a temporary rule and does data preparation:

```sh
export MY_IP=$(curl -s ifconfig.me) SG_ID=$(tofu -chdir=src/infra/aws output -raw rds_security_group_id) && \
aws ec2 authorize-security-group-ingress \
    --group-id "$SG_ID" \
    --protocol tcp \
    --port 5432 \
    --cidr "${MY_IP}/32" \
    --region "${TF_VAR_region:-ap-south-1}" && \
export DATABASE_URL="$(tofu -chdir=src/infra/aws output -raw rds_connection_string)" && \
python3 src/offline/simulate_company/setup_postgres.py && \
bash src/offline/index-policies/commands.sh
```

<details>
<summary>▶ Expected output</summary>

![alt text](src/offline/images/simulate_kestral.png)

</details>


### Phase 3: 

```sh
gh secret set AWS_ACCOUNT_ID --body $(aws sts get-caller-identity --query Account --output text)
echo " " >> src/workloads/agent-service/infra_tests.sh
echo " " >> src/workloads/mcp-server/test_locally.sh
gh secret set AWS_REGION --body $TF_VAR_region
git add . && git commit -m "Rebuilding mcp and agent docker images" && git push origin main
```
