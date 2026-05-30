variable "name_prefix" {
  type = string
}

variable "vpc_id" {
  type = string
}

resource "aws_security_group" "this" {
  # AWS provider docs support inline egress blocks; one block here expresses exactly one outbound rule.
  name        = "${var.name_prefix}-sg"
  description = "No inbound, outbound 443 only"
  vpc_id      = var.vpc_id

  egress {
    description = "HTTPS egress only"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.name_prefix}-sg"
  }
}

output "security_group_id" {
  value = aws_security_group.this.id
}