region      = "ap-south-1"
environment = "staging"
name_prefix = "agentops-staging"

vpc_cidr_block = "10.20.0.0/16"

azs = [
  "ap-south-1a",
  "ap-south-1b",
]

public_subnet_cidrs = [
  "10.20.1.0/24",
  "10.20.2.0/24",
]

tags = {
  Project = "agentops"
  Stack   = "staging"
}

bucket_name = "agentops-staging-embeddings-bucket"

# ECR repositories (lowercase with hyphens - best practice)
agent_repository_name = "agentops-staging-agent-service"
mcp_repository_name   = "agentops-staging-mcp-server"

# GitHub repository (exact case as on GitHub)
github_repository = "Athithya-Sakthivel/AgentOps"


force_destroy = true # ECR and S3 force_delete will also be true (same variable)
