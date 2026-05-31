output "endpoint" {
  description = "RDS instance endpoint (host:port)"
  value       = var.create_rds ? aws_db_instance.this[0].endpoint : null
}

output "address" {
  description = "RDS instance hostname"
  value       = var.create_rds ? aws_db_instance.this[0].address : null
}

output "port" {
  description = "RDS instance port"
  value       = var.create_rds ? aws_db_instance.this[0].port : null
}

output "db_name" {
  description = "Database name"
  value       = var.create_rds ? aws_db_instance.this[0].db_name : null
}

output "db_username" {
  description = "Master username"
  value       = var.create_rds ? var.db_username : null
}

output "connection_string" {
  description = "PostgreSQL connection URL (password included, sensitive)"
  value       = var.create_rds ? "postgresql://${var.db_username}:${local.effective_password}@${aws_db_instance.this[0].address}:${aws_db_instance.this[0].port}/${var.db_name}" : null
  sensitive   = true
}