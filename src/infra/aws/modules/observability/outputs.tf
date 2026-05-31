output "log_group_names" {
  value = {
    agent_service = aws_cloudwatch_log_group.agent_service.name
    mcp_server    = aws_cloudwatch_log_group.mcp_server.name
  }
}

output "dashboard_name" {
  value = aws_cloudwatch_dashboard.main.dashboard_name
}