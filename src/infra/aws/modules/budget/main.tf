variable "name_prefix" {
  type = string
}
variable "monthly_budget_amount" {
  type = number
}
variable "alert_emails" {
  type = list(string)
}
variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_budgets_budget" "monthly" {
  name         = "${var.name_prefix}-monthly-cost-budget"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_amount
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.alert_emails
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.alert_emails
  }

  cost_types {
    include_recurring          = true
    include_tax                = false
    include_refund             = false
    include_credit             = false
    include_upfront            = false
    include_other_subscription = false
    include_subscription       = false
    include_support            = false
    include_discount           = false
    use_blended                = false
  }

  tags = var.tags
}

output "budget_arn" {
  value = aws_budgets_budget.monthly.arn
}