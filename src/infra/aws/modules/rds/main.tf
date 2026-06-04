# ----------------------------------------------------------------------
# DATA SOURCES
# ----------------------------------------------------------------------
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ----------------------------------------------------------------------
# RANDOM PASSWORD (if not provided) – meets AWS complexity requirements
# ----------------------------------------------------------------------
resource "random_password" "db_password" {
  count = var.create_rds && var.db_password == null ? 1 : 0

  length  = 16
  special = true # Allows special characters
  upper   = true # Includes uppercase letters
  lower   = true # Includes lowercase letters
  numeric = true # Includes numbers

  # Exclude characters that are not permitted by RDS: @, /, ", space, and '
  override_special = "!#$%&()*+,-.;<=>?[]^_{|}~"
}

# ----------------------------------------------------------------------
# LOCAL: effective password (random or provided)
# ----------------------------------------------------------------------
locals {
  effective_password = var.db_password != null ? var.db_password : (var.create_rds ? random_password.db_password[0].result : null)
}

# ----------------------------------------------------------------------
# DB SUBNET GROUP (only if RDS is created)
# ----------------------------------------------------------------------
resource "aws_db_subnet_group" "this" {
  count = var.create_rds ? 1 : 0

  name_prefix = "${var.name_prefix}-rds-subnet-group"
  subnet_ids  = var.private_subnet_ids
  tags        = var.tags
}

# ----------------------------------------------------------------------
# RDS INSTANCE
# ----------------------------------------------------------------------
resource "aws_db_instance" "this" {
  count = var.create_rds ? 1 : 0

  identifier = "${var.name_prefix}-postgres"

  engine         = "postgres"
  engine_version = "18.4" # latest available as of mid‑2026
  instance_class = "db.t4g.micro"

  db_name  = var.db_name
  username = var.db_username
  password = local.effective_password

  allocated_storage     = 20
  max_allocated_storage = 100
  storage_encrypted     = true
  storage_type          = "gp3"

  db_subnet_group_name   = try(aws_db_subnet_group.this[0].name, null)
  vpc_security_group_ids = [var.security_group_id]

  publicly_accessible        = var.publicly_accessible
  skip_final_snapshot        = var.environment != "prod"
  deletion_protection        = var.environment == "prod"
  backup_retention_period    = var.environment == "prod" ? 7 : 1
  backup_window              = "03:00-04:00"
  maintenance_window         = "Mon:04:00-Mon:05:00"
  auto_minor_version_upgrade = true
  apply_immediately          = var.environment != "prod"

  performance_insights_enabled          = var.environment == "prod"
  performance_insights_retention_period = var.environment == "prod" ? 7 : 0

  tags = var.tags
}