# VPC
variable "vpc_cidr_block" {
  description = "CIDR block for the VPC"
  type        = string
}

variable "azs" {
  description = "List of Availability Zones to create subnets in (e.g. ['ap-south-1a', 'ap-south-1b'])"
  type        = list(string)
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets, one per Availability Zone"
  type        = list(string)
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets (e.g. for RDS), one per Availability Zone"
  type        = list(string)
  default     = []
}



# ----------------------------------------------------------------------
# S3 & ECR
# ----------------------------------------------------------------------
variable "bucket_name" {
  description = "Name of the S3 bucket for embeddings"
  type        = string
}

variable "force_destroy" {
  description = "Allow deletion of non‑empty S3 bucket and ECR repositories"
  type        = bool
  default     = false
}

variable "agent_repository_name" {
  description = "ECR repository name for agent‑service"
  type        = string
}

variable "mcp_repository_name" {
  description = "ECR repository name for mcp‑server"
  type        = string
}


# ----------------------------------------------------------------------
# GITHUB
# ----------------------------------------------------------------------
variable "github_repository" {
  description = "GitHub repository in owner/repo format"
  type        = string
}

# ----------------------------------------------------------------------
# CLOUDFLARE
# ----------------------------------------------------------------------
variable "cloudflare_tunnel_token" {
  description = "Cloudflare tunnel token (sensitive)"
  type        = string
  sensitive   = true
}

variable "cloudflare_hostname" {
  description = "Domain name used for Cloudflare tunnel ingress rules"
  type        = string
  default     = "athithya.site"
}


variable "create_rds" {
  description = "Whether to create RDS instance (false for staging, true for prod)"
  type        = bool
  default     = false
}

variable "db_password" {
  description = "Master password for RDS (optional, generates random)"
  type        = string
  sensitive   = true
  default     = null
}

variable "enable_ecs" {
  description = "Deploy ECS cluster and services (set to true for both staging and prod)"
  type        = bool
  default     = true
}

variable "alarm_sns_topic_arn" {
  description = "SNS topic ARN for CloudWatch alarms (optional)"
  type        = string
  default     = ""
}

variable "monthly_budget_amount" {
  description = "Monthly cost limit in USD"
  type        = number
  default     = 100
}

variable "alert_emails" {
  description = "List of email addresses for budget alerts"
  type        = list(string)
  default     = []
}