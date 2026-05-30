output "vpc_id" {
  value = module.vpc.vpc_id
}

output "public_subnet_ids" {
  value = module.vpc.public_subnet_ids
}

output "public_route_table_id" {
  value = module.vpc.public_route_table_id
}

output "security_group_id" {
  value = module.security_groups.security_group_id
}

output "bucket_name" {
  value = module.s3.bucket_name
}

output "bucket_arn" {
  value = module.s3.bucket_arn
}

output "agent_repository_url" {
  value = module.ecr_agent.repository_url
}

output "agent_repository_arn" {
  value = module.ecr_agent.arn
}

output "mcp_repository_url" {
  value = module.ecr_mcp.repository_url
}

output "mcp_repository_arn" {
  value = module.ecr_mcp.arn
}

output "github_agent_role_arn" {
  value = module.github_iam.github_role_arns["agent"]
}