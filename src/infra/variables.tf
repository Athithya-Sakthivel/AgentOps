variable "region" {
  type = string
}

variable "environment" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "vpc_cidr_block" {
  type = string
}

variable "azs" {
  type = list(string)
}

variable "public_subnet_cidrs" {
  type = list(string)
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "bucket_name" {
  type        = string
  description = "S3 bucket name per environment"
}

variable "force_destroy" {
  type        = bool
  description = "Allow destructive bucket deletion (staging only)"
}

variable "agent_repository_name" {
  type        = string
  description = "ECR repository name for the agent-service container"
}

variable "mcp_repository_name" {
  type        = string
  description = "ECR repository name for the mcp-server container"
}

variable "github_repository" {
  type        = string
  description = "GitHub repository in owner/repo format"
}