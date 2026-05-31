# ----------------------------------------------------------------------
# GitHub OIDC Provider (only if github_repositories is non‑empty)
# ----------------------------------------------------------------------
resource "aws_iam_openid_connect_provider" "github" {
  count = length(var.github_repositories) > 0 ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  tags           = var.tags
}

# ----------------------------------------------------------------------
# GitHub Actions Roles (one per repository)
# ----------------------------------------------------------------------
locals {
  github_repos = var.github_repositories
}

data "aws_iam_policy_document" "github_assume" {
  for_each = local.github_repos

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github[0].arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${each.value.github_repo}:ref:refs/heads/${each.value.branch}"]
    }
  }
}

data "aws_iam_policy_document" "github_ecr_push" {
  for_each = local.github_repos

  statement {
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

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
    resources = ["arn:aws:ecr:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:repository/${each.value.ecr_repository_name}"]
  }
}

resource "aws_iam_role" "github" {
  for_each = local.github_repos

  name               = each.value.role_name
  assume_role_policy = data.aws_iam_policy_document.github_assume[each.key].json
  tags               = var.tags
}

resource "aws_iam_policy" "github" {
  for_each = local.github_repos

  name   = "${each.value.role_name}-policy"
  policy = data.aws_iam_policy_document.github_ecr_push[each.key].json
  tags   = var.tags
}

resource "aws_iam_role_policy_attachment" "github" {
  for_each = local.github_repos

  role       = aws_iam_role.github[each.key].name
  policy_arn = aws_iam_policy.github[each.key].arn
}

# ----------------------------------------------------------------------
# OUTPUTS (only defined if roles were created)
# ----------------------------------------------------------------------
output "github_role_arns" {
  value = { for k, v in aws_iam_role.github : k => v.arn }
}