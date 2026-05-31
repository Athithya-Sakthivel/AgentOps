variable "create_rds" {
  description = "Whether to create the RDS instance (false for staging, true for prod)"
  type        = bool
  default     = false
}

variable "name_prefix" {
  description = "Prefix for resource naming"
  type        = string
}

variable "environment" {
  description = "Environment (staging, prod)"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID where the RDS instance will be deployed"
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs (RDS requires at least 2)"
  type        = list(string)
}

variable "security_group_id" {
  description = "Security group ID for RDS (controls inbound access from ECS)"
  type        = string
}

variable "db_name" {
  description = "Database name"
  type        = string
  default     = "kestral"
}

variable "db_username" {
  description = "Master username"
  type        = string
  default     = "agentops"
}

variable "db_password" {
  description = "Master password (if not provided, a random one is generated)"
  type        = string
  sensitive   = true
  default     = null
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}