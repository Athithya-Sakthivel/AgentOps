region      = "ap-south-1"
environment = "prod"
name_prefix = "agentops-prod"

vpc_cidr_block = "10.30.0.0/16"

azs = [
  "ap-south-1a",
  "ap-south-1b",
]

public_subnet_cidrs = [
  "10.30.1.0/24",
  "10.30.2.0/24",
]

tags = {
  Project = "agentops"
  Stack   = "prod"
}

bucket_name = "agentops-prod-embeddings-bucket"

# ECR repositories (lowercase with hyphens - best practice)
agent_repository_name = "agentops-prod-agent-service"
mcp_repository_name   = "agentops-prod-mcp-server"

# GitHub repository (exact case as on GitHub)
github_repository = "Athithya-Sakthivel/AgentOps"

force_destroy = false # production must NOT destroy non‑empty bucket and ECR repos
