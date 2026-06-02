output "vpc_id" {
  description = "ID of the VPC"
  value       = module.vpc.vpc_id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets"
  value       = module.vpc.public_subnet_ids
}

output "private_subnet_ids" {
  description = "IDs of the private subnets (empty list if none created)"
  value       = module.vpc.private_subnet_ids
}

output "public_route_table_id" {
  description = "ID of the public route table"
  value       = module.vpc.public_route_table_id
}


output "security_group_ids" {
  description = "Security group IDs"
  value = {
    ecs = module.security_groups.ecs_security_group_id
    rds = module.security_groups.rds_security_group_id
  }
}


output "s3_bucket" {
  description = "S3 bucket name and ARN"
  value = {
    name = module.s3.bucket_name
    arn  = module.s3.bucket_arn
  }
}

output "dynamodb_table" {
  description = "DynamoDB table name and ARN"
  value = {
    name = module.dynamodb.table_name
    arn  = module.dynamodb.table_arn
  }
}

output "ecr_repositories" {
  description = "ECR repository URLs and ARNs"
  value = {
    agent = {
      url = module.ecr_agent.repository_url
      arn = module.ecr_agent.arn
    }
    mcp = {
      url = module.ecr_mcp.repository_url
      arn = module.ecr_mcp.arn
    }
  }
}


output "ecs_cluster_id" {
  value = var.enable_ecs ? module.ecs_cluster[0].cluster_id : null
}

output "agent_service_name" {
  value = var.enable_ecs ? module.ecs_services[0].agent_service_name : null
}

output "mcp_service_name" {
  value = var.enable_ecs ? module.ecs_services[0].mcp_service_name : null
}