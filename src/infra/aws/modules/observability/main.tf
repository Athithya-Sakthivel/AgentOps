resource "aws_cloudwatch_log_group" "agent_service" {
  name              = "/ecs/${var.name_prefix}-agent-service"
  retention_in_days = var.retention_in_days
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "mcp_server" {
  name              = "/ecs/${var.name_prefix}-mcp-server"
  retention_in_days = var.retention_in_days
  tags              = var.tags
}

resource "aws_cloudwatch_log_metric_filter" "agent_requests" {
  name           = "${var.name_prefix}-agent-requests"
  pattern        = "{ $.message = \"Message processed\" }"
  log_group_name = aws_cloudwatch_log_group.agent_service.name

  metric_transformation {
    name      = "AgentRequests"
    namespace = "AgentOps"
    value     = "1"
  }
}

resource "aws_cloudwatch_log_metric_filter" "wallet_credits" {
  name           = "${var.name_prefix}-wallet-credits"
  pattern        = "{ $.message = \"Wallet credit issued\" }"
  log_group_name = aws_cloudwatch_log_group.mcp_server.name

  metric_transformation {
    name      = "WalletCredits"
    namespace = "AgentOps"
    value     = "1"
  }
}

resource "aws_cloudwatch_log_metric_filter" "tickets_created" {
  name           = "${var.name_prefix}-tickets-created"
  pattern        = "{ $.message = \"Ticket created\" }"
  log_group_name = aws_cloudwatch_log_group.mcp_server.name

  metric_transformation {
    name      = "TicketsCreated"
    namespace = "AgentOps"
    value     = "1"
  }
}

resource "aws_cloudwatch_log_metric_filter" "errors" {
  name           = "${var.name_prefix}-errors"
  pattern        = "{ $.level = \"ERROR\" }"
  log_group_name = aws_cloudwatch_log_group.agent_service.name

  metric_transformation {
    name      = "Errors"
    namespace = "AgentOps"
    value     = "1"
  }
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.name_prefix}-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AgentOps", "AgentRequests", { "stat" : "Sum", "period" : 300 }],
            ["AgentOps", "WalletCredits", { "stat" : "Sum", "period" : 300 }],
            ["AgentOps", "TicketsCreated", { "stat" : "Sum", "period" : 300 }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Agent Activity"
          period  = 300
          stat    = "Sum"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AgentOps", "Errors", { "stat" : "Sum", "period" : 300 }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Errors"
          period  = 300
          stat    = "Sum"
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 6
        width  = 24
        height = 6
        properties = {
          query  = "SOURCE '${aws_cloudwatch_log_group.agent_service.name}' | fields @timestamp, level, message, run_id | filter level = 'ERROR' | sort @timestamp desc | limit 20"
          region = var.aws_region
          title  = "Recent Errors"
          view   = "table"
        }
      }
    ]
  })
}

resource "aws_cloudwatch_metric_alarm" "errors_high" {
  count = var.alarm_sns_topic_arn != "" ? 1 : 0

  alarm_name          = "${var.name_prefix}-errors-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Errors"
  namespace           = "AgentOps"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  alarm_description   = "More than 5 errors in 5 minutes"
  alarm_actions       = [var.alarm_sns_topic_arn]
  treat_missing_data  = "notBreaching"
}
