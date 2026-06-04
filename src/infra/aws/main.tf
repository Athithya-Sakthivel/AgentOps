module "vpc" {
  source = "./modules/vpc"

  name_prefix          = var.name_prefix
  vpc_cidr_block       = var.vpc_cidr_block
  azs                  = var.azs
  public_subnet_cidrs  = var.public_subnet_cidrs
  private_subnet_cidrs = var.private_subnet_cidrs
  tags                 = var.tags
}


# ----------------------------------------------------------------------
# SECURITY GROUPS
# ----------------------------------------------------------------------
module "security_groups" {
  source = "./modules/security-groups"

  name_prefix = var.name_prefix
  vpc_id      = module.vpc.vpc_id
  tags        = var.tags
  # Optional: ssh_allowed_cidrs = ["203.0.113.0/24"]  # your office IP
}


# ----------------------------------------------------------------------
# S3 (Embeddings bucket)
# ----------------------------------------------------------------------
module "s3" {
  source = "./modules/s3"

  bucket_name   = var.bucket_name
  force_destroy = var.force_destroy
}

# ----------------------------------------------------------------------
# DYNAMODB (Rate limits)
# ----------------------------------------------------------------------
module "dynamodb" {
  source = "./modules/dynamodb"

  name         = "${var.name_prefix}-rate-limits"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"
  attributes = [
    { name = "pk", type = "S" },
    { name = "sk", type = "S" }
  ]
  ttl_enabled        = true
  ttl_attribute_name = "ttl"
  pitr_enabled       = var.environment == "prod"
  tags               = var.tags
}



# ----------------------------------------------------------------------
# RDS INSTANCE
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# RDS (PostgreSQL)
# ----------------------------------------------------------------------
module "rds" {
  source = "./modules/rds"

  create_rds  = var.create_rds
  name_prefix = var.name_prefix
  environment = var.environment
  vpc_id      = module.vpc.vpc_id
  # Instead of always private subnets, conditionally use public subnets
  private_subnet_ids  = var.rds_publicly_accessible ? module.vpc.public_subnet_ids : module.vpc.private_subnet_ids # for tutorials
  security_group_id   = module.security_groups.rds_security_group_id
  db_name             = "kestral"
  db_username         = "agentops"
  db_password         = var.db_password
  publicly_accessible = var.rds_publicly_accessible # also pass this flag
  tags                = var.tags
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
# IAM MODULES
# ----------------------------------------------------------------------

module "iam_ecs" {
  source = "./modules/iam"

  name_prefix           = var.name_prefix
  tags                  = var.tags
  embeddings_bucket_arn = module.s3.bucket_arn
  dynamodb_table_arn    = module.dynamodb.table_arn
  create_ecs_roles      = true # creates ECS roles
}

module "iam_github" {
  source = "./modules/iam"

  name_prefix = var.name_prefix
  tags        = var.tags

  # These are ignored when create_ecs_roles is false, but still required by the module
  embeddings_bucket_arn = module.s3.bucket_arn
  dynamodb_table_arn    = module.dynamodb.table_arn
  create_ecs_roles      = false # prevents creating ECS roles again

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
}

# ----------------------------------------------------------------------
# ECS CLUSTER (Managed Instances) – only if enabled
# ----------------------------------------------------------------------
module "ecs_cluster" {
  count = var.enable_ecs ? 1 : 0

  source = "./modules/ecs-cluster"

  cluster_name              = "${var.name_prefix}-cluster"
  instance_type             = "t4g.small"
  min_size                  = 2
  max_size                  = 2
  desired_capacity          = 2 # ASG for self healing not auto scaling
  security_group_id         = module.security_groups.ecs_security_group_id
  public_subnet_ids         = module.vpc.public_subnet_ids
  ecs_instance_profile_name = module.iam_ecs.ecs_instance_profile_name
  cloudflare_tunnel_token   = var.cloudflare_tunnel_token
  cloudflare_hostname       = var.cloudflare_hostname
  tags                      = var.tags
}

# ----------------------------------------------------------------------
# ECS TASKS & SERVICES – only if enabled
# ----------------------------------------------------------------------
module "ecs_services" {
  count = var.enable_ecs ? 1 : 0

  source = "./modules/ecs-services"

  cluster_name                 = "${var.name_prefix}-cluster"
  environment                  = var.environment
  aws_region                   = var.region
  cluster_id                   = module.ecs_cluster[0].cluster_id
  capacity_provider_name       = module.ecs_cluster[0].capacity_provider_name
  capacity_provider_dependency = module.ecs_cluster[0].capacity_provider_attachment

  ecs_execution_role_arn = module.iam_ecs.ecs_task_execution_role_arn
  agent_task_role_arn    = module.iam_ecs.agent_task_role_arn

  agent_image_url = module.ecr_agent.repository_url
  mcp_image_url   = module.ecr_mcp.repository_url
  agent_image_tag = "latest"
  mcp_image_tag   = "latest"

  agent_log_group_name = module.observability.log_group_names.agent_service
  mcp_log_group_name   = module.observability.log_group_names.mcp_server

  rds_connection_string = var.create_rds ? module.rds.connection_string : null

  agent_cpu           = 512
  agent_memory        = 712
  mcp_cpu             = 256
  mcp_memory          = 512
  agent_desired_count = 2
  mcp_desired_count   = 2

  tags = var.tags
}

# ----------------------------------------------------------------------
# OBSERVABILITY (CloudWatch logs, dashboard, alarms)
# ----------------------------------------------------------------------
module "observability" {
  source = "./modules/observability"

  name_prefix         = var.name_prefix
  environment         = var.environment
  aws_region          = var.region
  retention_in_days   = var.environment == "prod" ? 14 : 7
  alarm_sns_topic_arn = var.alarm_sns_topic_arn
  tags                = var.tags
}
