# ----------------------------------------------------------------------
# VARIABLES
# ----------------------------------------------------------------------
variable "name_prefix" {
  description = "Prefix to use for naming security groups"
  type        = string
}

variable "vpc_id" {
  description = "ID of the VPC where security groups will be created"
  type        = string
}

variable "tags" {
  description = "Common tags to apply to all resources"
  type        = map(string)
  default     = {}
}

# Optional: allow SSH from specific CIDRs (e.g., your office IP)
variable "ssh_allowed_cidrs" {
  description = "List of CIDR blocks allowed to SSH into ECS instances (optional)"
  type        = list(string)
  default     = []
}

# ----------------------------------------------------------------------
# ECS SECURITY GROUP (for EC2 instances running ECS tasks)
# ----------------------------------------------------------------------
resource "aws_security_group" "ecs" {
  name        = "${var.name_prefix}-ecs-sg"
  description = "Security group for ECS managed instances"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-ecs-sg"
  })
}

# Outbound: allow all traffic (EC2 needs to reach ECR, CloudWatch, etc.)
resource "aws_security_group_rule" "ecs_egress_all" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.ecs.id
}

# Optional inbound SSH (if you need to debug instances)
resource "aws_security_group_rule" "ecs_ingress_ssh" {
  count = length(var.ssh_allowed_cidrs) > 0 ? 1 : 0

  type              = "ingress"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = var.ssh_allowed_cidrs
  security_group_id = aws_security_group.ecs.id
}

# ----------------------------------------------------------------------
# RDS SECURITY GROUP (PostgreSQL only from ECS)
# ----------------------------------------------------------------------
resource "aws_security_group" "rds" {
  name        = "${var.name_prefix}-rds-sg"
  description = "Security group for RDS PostgreSQL"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-rds-sg"
  })
}

# Inbound PostgreSQL from the ECS security group
resource "aws_security_group_rule" "rds_ingress_postgres" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.ecs.id
  security_group_id        = aws_security_group.rds.id
}
