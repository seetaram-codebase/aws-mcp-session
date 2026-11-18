# "Finance Chat Web" -- the browser chat front end for the finance server
# (finance-chat-web/app.py). Same fixed-deployment shape as builder_web.tf:
# one always-on ECS service, its own ALB target group/listener rule, its
# own CloudFront domain, added to the shared Cognito pool's callback list
# like every other fixed server in this repo.
#
# This app is itself an OAuth *client* of the finance server (hand-rolled
# authorization-code + PKCE in app.py, using the finance server's own
# AWSCognitoProvider Dynamic Client Registration the same way builder-web
# does against the Launcher) -- it needs no Cognito-side configuration of
# its own beyond this one fixed callback URL.

resource "aws_ecr_repository" "finance_chat_web" {
  name                 = "mcp-finance-chat-web"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
}

resource "aws_iam_role" "finance_chat_web_ecs_task" {
  name               = "finance-chat-web-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "finance_chat_web_bedrock" {
  name = "bedrock-haiku-only"
  role = aws_iam_role.finance_chat_web_ecs_task.id
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

resource "aws_cloudwatch_log_group" "finance_chat_web" {
  name              = "/ecs/mcp-finance-chat-web"
  retention_in_days = 7
}

resource "aws_lb_target_group" "finance_chat_web" {
  name        = "mcp-finance-chat-web"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.default.id
  target_type = "ip"

  health_check {
    path    = "/api/session"
    matcher = "200"
  }
}

resource "aws_cloudfront_distribution" "finance_chat_web" {
  enabled = true

  origin {
    domain_name = aws_lb.mcp_server.dns_name
    origin_id   = "alb"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      origin_read_timeout    = 60
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

resource "aws_lb_listener_rule" "finance_chat_web" {
  listener_arn = aws_lb_listener.mcp_server.arn
  priority     = 12 # fixed, low -- same band as launcher.tf (10) / builder_web.tf (11); attendee rules still start at 100

  condition {
    host_header {
      values = [aws_cloudfront_distribution.finance_chat_web.domain_name]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.finance_chat_web.arn
  }
}

locals {
  finance_chat_web_public_url = "https://${aws_cloudfront_distribution.finance_chat_web.domain_name}"
}

resource "aws_ecs_task_definition" "finance_chat_web" {
  family                   = "mcp-finance-chat-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.finance_chat_web_ecs_task.arn

  container_definitions = jsonencode([
    {
      name         = "mcp-finance-chat-web"
      image        = "${aws_ecr_repository.finance_chat_web.repository_url}:${var.finance_chat_web_image_tag}"
      essential    = true
      portMappings = [{ containerPort = 8000, protocol = "tcp" }]
      environment = [
        { name = "FINANCE_MCP_BASE_URL", value = local.mcp_public_url },
        { name = "PUBLIC_BASE_URL", value = local.finance_chat_web_public_url },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "PORT", value = "8000" },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.finance_chat_web.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "mcp-finance-chat-web"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "finance_chat_web" {
  name            = "mcp-finance-chat-web"
  cluster         = aws_ecs_cluster.mcp_demo.id
  task_definition = aws_ecs_task_definition.finance_chat_web.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.finance_chat_web.arn
    container_name   = "mcp-finance-chat-web"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener_rule.finance_chat_web]
}
