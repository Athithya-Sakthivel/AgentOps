variable "cluster_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "cluster_id" {
  type = string
}

variable "capacity_provider_name" {
  type = string
}

variable "capacity_provider_dependency" {
  type    = any
  default = null
}

variable "ecs_execution_role_arn" {
  type = string
}

variable "agent_task_role_arn" {
  type = string
}

variable "agent_image_url" {
  type = string
}

variable "mcp_image_url" {
  type = string
}

variable "agent_image_tag" {
  type    = string
  default = "latest"
}

variable "mcp_image_tag" {
  type    = string
  default = "latest"
}

variable "agent_log_group_name" {
  type = string
}

variable "mcp_log_group_name" {
  type = string
}

variable "rds_connection_string" {
  type      = string
  default   = null
  sensitive = true
}

variable "agent_cpu" {
  type    = number
  default = 512
}

variable "agent_memory" {
  type    = number
  default = 1024
}

variable "mcp_cpu" {
  type    = number
  default = 256
}

variable "mcp_memory" {
  type    = number
  default = 512
}

variable "agent_desired_count" {
  type    = number
  default = 2
}

variable "mcp_desired_count" {
  type    = number
  default = 2
}

variable "tags" {
  type    = map(string)
  default = {}
}