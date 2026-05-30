# ----------------------------------------------------------------------
# VARIABLES
# ----------------------------------------------------------------------
variable "name" {
  description = "Name of the ECR repository (can include uppercase letters)"
  type        = string
}

variable "force_delete" {
  description = "If true, delete the repository even if it contains images"
  type        = bool
  default     = false
}

# ----------------------------------------------------------------------
# ECR REPOSITORY
# ----------------------------------------------------------------------
resource "aws_ecr_repository" "this" {
  name = var.name

  # Prevent tag overwrites – official argument
  image_tag_mutability = "IMMUTABLE"

  # Vulnerability scan on every push
  image_scanning_configuration {
    scan_on_push = true
  }

  # Allow forced deletion (controlled by var.force_delete)
  force_delete = var.force_delete

  tags = {
    Name = var.name
  }
}

# ----------------------------------------------------------------------
# LIFECYCLE POLICY – Updated to comply with AWS API rules
# ----------------------------------------------------------------------
resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 50 tagged images (with 'v' or 'latest' prefix)"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["v", "latest"]
          countType     = "imageCountMoreThan"
          countNumber   = 50
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Expire untagged images older than 14 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 14
        }
        action = { type = "expire" }
      }
    ]
  })
}

# ----------------------------------------------------------------------
# OUTPUTS
# ----------------------------------------------------------------------
output "arn" {
  value = aws_ecr_repository.this.arn
}

output "repository_url" {
  value = aws_ecr_repository.this.repository_url
}