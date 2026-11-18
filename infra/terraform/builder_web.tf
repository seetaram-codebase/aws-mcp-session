# "MCP Builder Web" -- the click-to-deploy front end for the Launcher
# (builder-web/app.py). Same fixed-deployment shape as launcher.tf: one
# always-on ECS service, its own ALB target group/listener rule, its own
# CloudFront domain, added to the shared Cognito pool's callback list like
# every other fixed server in this repo.
#
# This app is itself an OAuth *client* of the Launcher (hand-rolled
# authorization-code + PKCE in app.py, using the Launcher's Dynamic Client
# Registration the same way Inspector/submit.py/Electron do) -- it needs no
# Cognito-side configuration of its own beyond this one fixed callback URL,
# which is between this app and the Launcher's OAuth proxy, not Cognito
# directly.

resource "aws_ecr_repository" "builder_web" {
  name                 = "mcp-builder-web"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
}

resource "aws_iam_role" "builder_web_ecs_task" {
  name               = "builder-web-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "builder_web_bedrock" {
  name = "bedrock-haiku-only"
  role = aws_iam_role.builder_web_ecs_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["bedrock:InvokeModel", "bedrock:Converse"]
      Resource = [
        "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
      ]
    }]
  })
}

resource "aws_cloudwatch_log_group" "builder_web" {
  name              = "/ecs/mcp-builder-web"
  retention_in_days = 7
}

resource "aws_lb_target_group" "builder_web" {
  name        = "mcp-builder-web"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.default.id
  target_type = "ip"

  health_check {
    path    = "/api/session"
    matcher = "200"
  }
}

resource "aws_cloudfront_distribution" "builder_web" {
  enabled = true

  origin {
    domain_name = aws_lb.mcp_server.dns_name
    origin_id   = "alb"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "alb"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]

    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # Managed-CachingDisabled
    origin_request_policy_id = "216adef6-5c7f-47e4-b989-5492eafa07d3" # Managed-AllViewer
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

resource "aws_lb_listener_rule" "builder_web" {
  listener_arn = aws_lb_listener.mcp_server.arn
  priority     = 11 # fixed, low -- same band as launcher.tf's rule (10); attendee rules still start at 100

  condition {
    host_header {
      values = [aws_cloudfront_distribution.builder_web.domain_name]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.builder_web.arn
  }
}

locals {
  builder_web_public_url = "https://${aws_cloudfront_distribution.builder_web.domain_name}"
}

resource "aws_ecs_task_definition" "builder_web" {
  family                   = "mcp-builder-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.builder_web_ecs_task.arn

  container_definitions = jsonencode([
    {
      name         = "mcp-builder-web"
      image        = "${aws_ecr_repository.builder_web.repository_url}:${var.builder_web_image_tag}"
      essential    = true
      portMappings = [{ containerPort = 8080, protocol = "tcp" }]
      environment = [
        { name = "LAUNCHER_BASE_URL", value = local.launcher_public_url },
        { name = "PUBLIC_BASE_URL", value = local.builder_web_public_url },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "PORT", value = "8080" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.builder_web.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "mcp-builder-web"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "builder_web" {
  name            = "mcp-builder-web"
  cluster         = aws_ecs_cluster.mcp_demo.id
  task_definition = aws_ecs_task_definition.builder_web.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.builder_web.arn
    container_name   = "mcp-builder-web"
    container_port   = 8080
  }

  depends_on = [aws_lb_listener_rule.builder_web]
}
