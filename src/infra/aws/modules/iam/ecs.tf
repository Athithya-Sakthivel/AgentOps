# ----------------------------------------------------------------------
# ECS EC2 INSTANCE PROFILE (only if create_ecs_roles == true)
# ----------------------------------------------------------------------
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "aws_iam_role" "ecs_instance_role" {
  count = var.create_ecs_roles ? 1 : 0

  name = "${var.name_prefix}-ecs-instance-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "ecs_instance_role_policy" {
  count = var.create_ecs_roles ? 1 : 0

  role       = aws_iam_role.ecs_instance_role[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_instance_profile" "ecs_instance_profile" {
  count = var.create_ecs_roles ? 1 : 0

  name = "${var.name_prefix}-ecs-instance-profile"
  role = aws_iam_role.ecs_instance_role[0].name
  tags = var.tags
}

# ----------------------------------------------------------------------
# ECS TASK EXECUTION ROLE (common for all tasks)
# ----------------------------------------------------------------------
resource "aws_iam_role" "ecs_task_execution_role" {
  count = var.create_ecs_roles ? 1 : 0

  name = "${var.name_prefix}-ecs-task-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_role_policy" {
  count = var.create_ecs_roles ? 1 : 0

  role       = aws_iam_role.ecs_task_execution_role[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ----------------------------------------------------------------------
# TASK ROLE FOR AGENT SERVICE (only agent-service needs AWS APIs)
# ----------------------------------------------------------------------
resource "aws_iam_role" "agent_task_role" {
  count = var.create_ecs_roles ? 1 : 0

  name = "${var.name_prefix}-agent-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = var.tags
}

data "aws_iam_policy_document" "agent_task_policy" {
  count = var.create_ecs_roles ? 1 : 0

  # SSM Parameter Store – read secrets
  statement {
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter/agentops/*"]
  }

  # KMS – decrypt (if SSM SecureString used)
  statement {
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [var.kms_key_arn != null ? var.kms_key_arn : "arn:aws:kms:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:key/*"]
  }

  # Bedrock
  statement {
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:ListFoundationModels"
    ]
    resources = ["arn:aws:bedrock:${data.aws_region.current.region}::foundation-model/*"]
  }

  # S3 – embeddings bucket
  statement {
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:ListBucket"]
    resources = [
      var.embeddings_bucket_arn,
      "${var.embeddings_bucket_arn}/*"
    ]
  }

  # DynamoDB – rate limits
  statement {
    effect    = "Allow"
    actions   = ["dynamodb:UpdateItem", "dynamodb:GetItem"]
    resources = [var.dynamodb_table_arn]
  }

  # CloudWatch Logs & Metrics
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
      "cloudwatch:PutMetricData"
    ]
    resources = ["*"]
  }

  # ECR – pull images (already covered by execution role, but kept for completeness)
  statement {
    effect = "Allow"
    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage"
    ]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "agent_task_policy" {
  count = var.create_ecs_roles ? 1 : 0

  name        = "${var.name_prefix}-agent-task-policy"
  description = "Policy for agent-service task role"
  policy      = data.aws_iam_policy_document.agent_task_policy[0].json
  tags        = var.tags
}

resource "aws_iam_role_policy_attachment" "agent_task_policy_attachment" {
  count = var.create_ecs_roles ? 1 : 0

  role       = aws_iam_role.agent_task_role[0].name
  policy_arn = aws_iam_policy.agent_task_policy[0].arn
}

# ----------------------------------------------------------------------
# OUTPUTS (conditional)
# ----------------------------------------------------------------------
output "ecs_instance_profile_name" {
  value = var.create_ecs_roles ? aws_iam_instance_profile.ecs_instance_profile[0].name : null
}

output "ecs_task_execution_role_arn" {
  value = var.create_ecs_roles ? aws_iam_role.ecs_task_execution_role[0].arn : null
}

output "agent_task_role_arn" {
  value = var.create_ecs_roles ? aws_iam_role.agent_task_role[0].arn : null
}