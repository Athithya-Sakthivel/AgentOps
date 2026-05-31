data "aws_ami" "ecs_optimized" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name = "name"
    # Matches the ARM64 variant of the ECS-optimized AMI
    values = ["amzn2-ami-ecs-hvm-*-arm64-ebs"]
  }

  # This filter ensures the AMI architecture matches your Graviton instances
  filter {
    name   = "architecture"
    values = ["arm64"]
  }
}

resource "aws_launch_template" "main" {
  name_prefix   = "${var.cluster_name}-lt"
  image_id      = data.aws_ami.ecs_optimized.id
  instance_type = var.instance_type

  vpc_security_group_ids = [var.security_group_id]

  user_data = base64encode(templatefile("${path.module}/user_data.sh", {
    cluster_name            = var.cluster_name
    cloudflare_tunnel_token = var.cloudflare_tunnel_token
    cloudflare_hostname     = var.cloudflare_hostname
  }))

  iam_instance_profile {
    name = var.ecs_instance_profile_name
  }

  tag_specifications {
    resource_type = "instance"
    tags = merge(var.tags, {
      Name = "${var.cluster_name}-instance"
    })
  }

  tags = var.tags
}

resource "aws_autoscaling_group" "main" {
  name_prefix         = "${var.cluster_name}-asg"
  vpc_zone_identifier = var.public_subnet_ids

  launch_template {
    id      = aws_launch_template.main.id
    version = "$Latest"
  }

  min_size         = var.min_size
  max_size         = var.max_size
  desired_capacity = var.desired_capacity

  # Required to satisfy the ECS capacity provider's managed termination protection
  protect_from_scale_in = true

  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = true
  }

  tag {
    key                 = "Name"
    value               = "${var.cluster_name}-instance"
    propagate_at_launch = true
  }

  dynamic "tag" {
    for_each = var.tags
    content {
      key                 = tag.key
      value               = tag.value
      propagate_at_launch = true
    }
  }
}

resource "aws_ecs_capacity_provider" "main" {
  name = "${var.cluster_name}-cp"

  auto_scaling_group_provider {
    auto_scaling_group_arn = aws_autoscaling_group.main.arn

    managed_scaling {
      status          = "ENABLED"
      target_capacity = 100
    }

    managed_termination_protection = "ENABLED"
  }
}

resource "aws_ecs_cluster" "main" {
  name = var.cluster_name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = var.tags
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = [aws_ecs_capacity_provider.main.name]

  default_capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.main.name
    weight            = 1
    base              = 1
  }
}

output "cluster_id" {
  value = aws_ecs_cluster.main.id
}

output "cluster_arn" {
  value = aws_ecs_cluster.main.arn
}

output "capacity_provider_name" {
  value = aws_ecs_capacity_provider.main.name
}

output "capacity_provider_attachment" {
  value = aws_ecs_cluster_capacity_providers.main
}