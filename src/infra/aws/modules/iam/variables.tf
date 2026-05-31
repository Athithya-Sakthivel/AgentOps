variable "name_prefix" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "kms_key_arn" {
  type    = string
  default = null
}

variable "embeddings_bucket_arn" {
  type = string
}

variable "dynamodb_table_arn" {
  type = string
}

variable "create_ecs_roles" {
  description = "Whether to create ECS instance profile, execution role, and agent task role"
  type        = bool
  default     = false
}

variable "github_repositories" {
  description = "Map of GitHub OIDC role definitions (optional)"
  type = map(object({
    ecr_repository_name = string
    github_repo         = string
    branch              = string
    role_name           = string
  }))
  default = {}
}