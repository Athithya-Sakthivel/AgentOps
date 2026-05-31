data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "random_password" "db_password" {
  count   = var.create_rds && var.db_password == null ? 1 : 0
  length  = 16
  special = false
}

locals {
  effective_password = var.db_password != null ? var.db_password : (var.create_rds ? random_password.db_password[0].result : null)
}

resource "aws_db_subnet_group" "this" {
  count = var.create_rds ? 1 : 0

  name_prefix = "${var.name_prefix}-rds-subnet-group"
  subnet_ids  = var.private_subnet_ids
  tags        = var.tags
}

resource "aws_db_instance" "this" {
  count = var.create_rds ? 1 : 0

  identifier = "${var.name_prefix}-postgres"

  engine         = "postgres"
  engine_version = "16.4"
  instance_class = "db.t4g.micro"

  db_name  = var.db_name
  username = var.db_username
  password = local.effective_password

  allocated_storage     = 20
  max_allocated_storage = 100
  storage_encrypted     = true
  storage_type          = "gp3"

  db_subnet_group_name   = aws_db_subnet_group.this[0].name
  vpc_security_group_ids = [var.security_group_id]

  publicly_accessible        = false
  skip_final_snapshot        = var.environment != "prod"
  deletion_protection        = var.environment == "prod"
  backup_retention_period    = 7
  backup_window              = "03:00-04:00"
  maintenance_window         = "Mon:04:00-Mon:05:00"
  auto_minor_version_upgrade = true
  apply_immediately          = var.environment != "prod"

  performance_insights_enabled          = var.environment == "prod"
  performance_insights_retention_period = var.environment == "prod" ? 7 : 0

  tags = var.tags
}