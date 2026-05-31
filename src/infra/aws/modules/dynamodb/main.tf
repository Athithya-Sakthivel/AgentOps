# ----------------------------------------------------------------------
# VARIABLES (module inputs)
# ----------------------------------------------------------------------
variable "name" {
  description = "Name of the DynamoDB table. Must be unique within an AWS region."
  type        = string
}

variable "billing_mode" {
  description = "Controls how you are charged for read and write throughput and how you manage capacity."
  type        = string
  default     = "PAY_PER_REQUEST"
}

variable "hash_key" {
  description = "The attribute to use as the hash (partition) key."
  type        = string
}

variable "range_key" {
  description = "The attribute to use as the range (sort) key."
  type        = string
  default     = null
}

variable "attributes" {
  description = "List of nested attribute definitions. Only required for hash_key and range_key attributes."
  type = list(object({
    name = string
    type = string
  }))
}

variable "ttl_enabled" {
  description = "Indicates whether TTL is enabled."
  type        = bool
  default     = true
}

variable "ttl_attribute_name" {
  description = "The name of the table attribute to store the TTL timestamp in."
  type        = string
  default     = "ttl"
}

variable "pitr_enabled" {
  description = "Indicates whether point-in-time recovery is enabled."
  type        = bool
  default     = false
}

variable "pitr_recovery_period_in_days" {
  description = "Number of preceding days for which continuous backups are taken and maintained. Default is 35."
  type        = number
  default     = 35
}

variable "server_side_encryption_enabled" {
  description = "Indicates whether server-side encryption is enabled."
  type        = bool
  default     = true
}

variable "tags" {
  description = "A map of tags to assign to the table."
  type        = map(string)
  default     = {}
}

# ----------------------------------------------------------------------
# DYNAMODB TABLE
# ----------------------------------------------------------------------
resource "aws_dynamodb_table" "this" {
  name         = var.name
  billing_mode = var.billing_mode
  hash_key     = var.hash_key
  range_key    = var.range_key

  dynamic "attribute" {
    for_each = var.attributes
    content {
      name = attribute.value.name
      type = attribute.value.type
    }
  }

  ttl {
    enabled        = var.ttl_enabled
    attribute_name = var.ttl_attribute_name
  }

  point_in_time_recovery {
    enabled                 = var.pitr_enabled
    recovery_period_in_days = var.pitr_recovery_period_in_days
  }

  server_side_encryption {
    enabled = var.server_side_encryption_enabled
  }

  tags = var.tags
}

# ----------------------------------------------------------------------
# OUTPUTS
# ----------------------------------------------------------------------
output "table_name" {
  description = "The name of the DynamoDB table."
  value       = aws_dynamodb_table.this.name
}

output "table_arn" {
  description = "The ARN of the DynamoDB table."
  value       = aws_dynamodb_table.this.arn
}