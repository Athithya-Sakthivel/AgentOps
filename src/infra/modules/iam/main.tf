# ----------------------------------------------------------------------
# VARIABLES
# ----------------------------------------------------------------------
variable "github_repositories" {
  description = "Map of repository identifiers to ECR repository names"
  type = map(object({
    ecr_repository_name = string
    github_repo         = string
    branch              = string
    role_name           = string
  }))
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}

# ----------------------------------------------------------------------
# OIDC PROVIDER FOR GITHUB ACTIONS
# ----------------------------------------------------------------------
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  tags           = var.tags
}

# ----------------------------------------------------------------------
# TRUST POLICY FOR GITHUB ACTIONS (OIDC)
# ----------------------------------------------------------------------
data "aws_iam_policy_document" "github_assume" {
  for_each = var.github_repositories

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${each.value.github_repo}:ref:refs/heads/${each.value.branch}"
      ]
    }
  }
}

# ----------------------------------------------------------------------
# ECR PERMISSIONS POLICY (ALLOW PUSH/PULL FOR ONE REPOSITORY)
# ----------------------------------------------------------------------
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_iam_policy_document" "github_ecr" {
  for_each = var.github_repositories

  # GetAuthorizationToken requires "*" resource
  statement {
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # All other actions limited to the specific repository
  statement {
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchDeleteImage",
      "ecr:DescribeImages",
      "ecr:DeleteImage",
    ]
    resources = [
      # FIX: .region is the correct, non-deprecated attribute
      "arn:aws:ecr:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:repository/${each.value.ecr_repository_name}"
    ]
  }
}

# ----------------------------------------------------------------------
# IAM ROLE FOR EACH GITHUB REPOSITORY
# ----------------------------------------------------------------------
resource "aws_iam_role" "github" {
  for_each = var.github_repositories

  name               = each.value.role_name
  assume_role_policy = data.aws_iam_policy_document.github_assume[each.key].json
  tags               = var.tags
}

resource "aws_iam_policy" "github" {
  for_each = var.github_repositories

  name        = "${each.value.role_name}-policy"
  description = "Allows ECR push/pull to ${each.value.ecr_repository_name}"
  policy      = data.aws_iam_policy_document.github_ecr[each.key].json
  tags        = var.tags
}

resource "aws_iam_role_policy_attachment" "github" {
  for_each = var.github_repositories

  role       = aws_iam_role.github[each.key].name
  policy_arn = aws_iam_policy.github[each.key].arn
}

# ----------------------------------------------------------------------
# OUTPUTS
# ----------------------------------------------------------------------
output "github_oidc_provider_arn" {
  value = aws_iam_openid_connect_provider.github.arn
}

output "github_role_arns" {
  description = "Map of GitHub repo key → IAM role ARN"
  value = {
    for k, v in aws_iam_role.github : k => v.arn
  }
}

output "github_role_names" {
  description = "Map of GitHub repo key → IAM role name"
  value = {
    for k, v in aws_iam_role.github : k => v.name
  }
}