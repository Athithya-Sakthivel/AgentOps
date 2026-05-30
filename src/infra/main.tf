module "vpc" {
  source = "./modules/vpc"

  name_prefix         = var.name_prefix
  vpc_cidr_block      = var.vpc_cidr_block
  azs                 = var.azs
  public_subnet_cidrs = var.public_subnet_cidrs
}

module "security_groups" {
  source = "./modules/security-groups"

  name_prefix = var.name_prefix
  vpc_id      = module.vpc.vpc_id
}

module "s3" {
  source = "./modules/s3"

  bucket_name   = var.bucket_name
  force_destroy = var.force_destroy
}

# ----------------------------------------------------------------------
# ECR REPOSITORIES
# ----------------------------------------------------------------------
module "ecr_agent" {
  source = "./modules/ecr"

  name         = var.agent_repository_name
  force_delete = var.force_destroy
}

module "ecr_mcp" {
  source = "./modules/ecr"

  name         = var.mcp_repository_name
  force_delete = var.force_destroy
}

# ----------------------------------------------------------------------
# IAM ROLES FOR GITHUB ACTIONS
# ----------------------------------------------------------------------
module "github_iam" {
  source = "./modules/iam"

  github_repositories = {
    agent = {
      ecr_repository_name = var.agent_repository_name
      github_repo         = var.github_repository
      branch              = "main"
      role_name           = "gh-actions-agentops-agent"
    }
    mcp = {
      ecr_repository_name = var.mcp_repository_name
      github_repo         = var.github_repository
      branch              = "main"
      role_name           = "gh-actions-agentops-mcp"
    }
  }
  tags = var.tags
}