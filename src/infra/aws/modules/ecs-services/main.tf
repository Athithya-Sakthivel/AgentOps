# ----------------------------------------------------------------------
# agent-service task definition (needs task role)
# ----------------------------------------------------------------------
resource "aws_ecs_task_definition" "agent" {
  family                   = "${var.cluster_name}-agent"
  network_mode             = "bridge"
  requires_compatibilities = ["EC2"]
  cpu                      = var.agent_cpu
  memory                   = var.agent_memory
  execution_role_arn       = var.ecs_execution_role_arn
  task_role_arn            = var.agent_task_role_arn

  container_definitions = jsonencode([
    {
      name      = "agent-service"
      image     = "${var.agent_image_url}:${var.agent_image_tag}"
      essential = true
      portMappings = [
        {
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        }
      ]
      environment = concat(
        var.rds_connection_string != null ? [
          {
            name  = "DATABASE_URL"
            value = var.rds_connection_string
          }
        ] : [],
        [
          {
            name  = "ENVIRONMENT"
            value = var.environment
          }
        ]
      )
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.agent_log_group_name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = var.tags
}

# ----------------------------------------------------------------------
# mcp-server task definition (no task role)
# ----------------------------------------------------------------------
resource "aws_ecs_task_definition" "mcp" {
  family                   = "${var.cluster_name}-mcp"
  network_mode             = "bridge"
  requires_compatibilities = ["EC2"]
  cpu                      = var.mcp_cpu
  memory                   = var.mcp_memory
  execution_role_arn       = var.ecs_execution_role_arn
  # task_role_arn omitted

  container_definitions = jsonencode([
    {
      name      = "mcp-server"
      image     = "${var.mcp_image_url}:${var.mcp_image_tag}"
      essential = true
      portMappings = [
        {
          containerPort = 8001
          hostPort      = 8001
          protocol      = "tcp"
        }
      ]
      environment = concat(
        var.rds_connection_string != null ? [
          {
            name  = "DATABASE_URL"
            value = var.rds_connection_string
          }
        ] : [],
        [
          {
            name  = "ENVIRONMENT"
            value = var.environment
          }
        ]
      )
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.mcp_log_group_name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = var.tags
}

# ----------------------------------------------------------------------
# ECS service: agent-service
# ----------------------------------------------------------------------
resource "aws_ecs_service" "agent" {
  name            = "${var.cluster_name}-agent"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.agent.arn
  desired_count   = var.agent_desired_count

  capacity_provider_strategy {
    capacity_provider = var.capacity_provider_name
    weight            = 1
    base              = 1
  }

  placement_constraints {
    type = "distinctInstance"
  }

  depends_on = [var.capacity_provider_dependency]

  tags = var.tags
}

# ----------------------------------------------------------------------
# ECS service: mcp-server
# ----------------------------------------------------------------------
resource "aws_ecs_service" "mcp" {
  name            = "${var.cluster_name}-mcp"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.mcp.arn
  desired_count   = var.mcp_desired_count

  capacity_provider_strategy {
    capacity_provider = var.capacity_provider_name
    weight            = 1
    base              = 1
  }

  placement_constraints {
    type = "distinctInstance"
  }

  tags = var.tags
}

# ----------------------------------------------------------------------
# OUTPUTS
# ----------------------------------------------------------------------
output "agent_service_name" {
  value = aws_ecs_service.agent.name
}

output "mcp_service_name" {
  value = aws_ecs_service.mcp.name
}