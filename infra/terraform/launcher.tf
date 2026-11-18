# The "MCP Builder" Launcher: one always-on FastMCP server (same shape as
# ecs.tf's finance server) whose one tool, submit_server, provisions a brand
# new, dedicated ECS task per attendee via boto3 -- NOT via Terraform. This
# file only manages the FIXED pieces: the Launcher's own deployment, the two
# IAM roles, and the one shared Docker image attendee tasks all reuse.
#
# Attendee tasks/target-groups/listener-rules/CloudFront-distributions and
# the growing Cognito callback_urls list are created and owned imperatively
# by launcher/launcher_server.py at runtime, not by Terraform -- which means
# `terraform plan` will show permanent drift on
# aws_cognito_user_pool_client.this.callback_urls once attendees start
# submitting. That's expected, not a bug: Terraform still owns the
# *original* fixed callback URLs (see main.tf); it just doesn't try to
# reconcile the ones the Launcher adds afterward.

# ---------------------------------------------------------------------------
# ECR -- one repo for the Launcher image, one for the shared attendee base
# image (built once, reused for every attendee submission).
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "launcher" {
  name                 = "mcp-launcher"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
}

resource "aws_ecr_repository" "attendee_base" {
  name                 = "mcp-attendee-base"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
}

# ---------------------------------------------------------------------------
# IAM -- two different shapes, same philosophy as ecs.tf's execution/task
# split: the attendee role is deliberately narrow (Bedrock only, nothing
# else); the Launcher's own role is genuinely privileged because its whole
# job is provisioning AWS infrastructure on attendees' behalf.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "attendee_ecs_task" {
  name               = "mcp-attendee-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "attendee_bedrock" {
  name = "bedrock-haiku-only"
  role = aws_iam_role.attendee_ecs_task.id
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

data "aws_caller_identity" "current" {}

resource "aws_iam_role" "launcher_ecs_task" {
  name               = "launcher-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "launcher_provisioning" {
  name = "attendee-server-provisioning"
  role = aws_iam_role.launcher_ecs_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EcsAttendeeServices"
        Effect = "Allow"
        Action = [
          "ecs:RegisterTaskDefinition",
          "ecs:ListServices",
          "ecs:DescribeServices",
        ]
        Resource = "*" # RegisterTaskDefinition/List/Describe don't support resource-level scoping
      },
      {
        Sid      = "EcsCreateAttendeeServiceOnly"
        Effect   = "Allow"
        Action   = ["ecs:CreateService"]
        Resource = "arn:aws:ecs:*:${data.aws_caller_identity.current.account_id}:service/${aws_ecs_cluster.mcp_demo.name}/mcp-attendee-*"
      },
      {
        Sid      = "PassAttendeeRolesOnly"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.attendee_ecs_task.arn, aws_iam_role.ecs_task_execution.arn]
      },
      {
        Sid      = "AlbTargetGroupsAndRules"
        Effect   = "Allow"
        Action   = ["elasticloadbalancing:CreateTargetGroup", "elasticloadbalancing:CreateRule", "elasticloadbalancing:DescribeRules", "elasticloadbalancing:DescribeListeners", "elasticloadbalancing:DescribeLoadBalancers"]
        Resource = "*" # ELBv2 create-time actions don't support resource-level scoping
      },
      {
        Sid      = "CloudFrontDistributions"
        Effect   = "Allow"
        Action   = ["cloudfront:CreateDistribution"]
        Resource = "*" # CreateDistribution doesn't support resource-level scoping
      },
      {
        Sid      = "CognitoCallbackUrlUpdates"
        Effect   = "Allow"
        Action   = ["cognito-idp:DescribeUserPoolClient", "cognito-idp:UpdateUserPoolClient"]
        Resource = aws_cognito_user_pool.this.arn
      },
      {
        Sid      = "AttendeeLogGroups"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:PutRetentionPolicy"]
        Resource = "arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/ecs/mcp-attendee-*"
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Launcher's own deployment -- same ALB (new target group + listener rule),
# its own CloudFront distribution/domain, its own ECS service. Fixed and
# Terraform-managed, unlike the attendee servers it creates at runtime.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "launcher" {
  name              = "/ecs/mcp-launcher"
  retention_in_days = 7
}

resource "aws_lb_target_group" "launcher" {
  name        = "mcp-launcher"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.default.id
  target_type = "ip"

  health_check {
    path    = "/mcp"
    matcher = "200,401"
  }
}

resource "aws_cloudfront_distribution" "launcher" {
  enabled = true

  origin {
    domain_name = aws_lb.mcp_server.dns_name
    origin_id   = "alb"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      # Max allowed (default 30s) -- submit_server's boto3 provisioning can
      # run long; give it the most headroom CloudFront allows rather than
      # have a slow-but-successful request come back as a hard failure.
      origin_read_timeout = 60
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

resource "aws_lb_listener_rule" "launcher" {
  listener_arn = aws_lb_listener.mcp_server.arn
  priority     = 10 # fixed, low -- attendee rules start at 100 (see launcher_server.py's _next_priority)

  condition {
    host_header {
      values = [aws_cloudfront_distribution.launcher.domain_name]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.launcher.arn
  }
}

locals {
  launcher_public_url = "https://${aws_cloudfront_distribution.launcher.domain_name}"
}

resource "aws_ecs_task_definition" "launcher" {
  family                   = "mcp-launcher"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.launcher_ecs_task.arn

  container_definitions = jsonencode([
    {
      name         = "mcp-launcher"
      image        = "${aws_ecr_repository.launcher.repository_url}:${var.launcher_image_tag}"
      essential    = true
      portMappings = [{ containerPort = 8000, protocol = "tcp" }]
      environment = [
        { name = "COGNITO_USER_POOL_ID", value = aws_cognito_user_pool.this.id },
        { name = "COGNITO_CLIENT_ID", value = aws_cognito_user_pool_client.this.id },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "MCP_BASE_URL", value = local.launcher_public_url },
        { name = "PORT", value = "8000" },
        { name = "ECS_CLUSTER_NAME", value = aws_ecs_cluster.mcp_demo.name },
        { name = "ATTENDEE_IMAGE_URI", value = "${aws_ecr_repository.attendee_base.repository_url}:${var.attendee_base_image_tag}" },
        { name = "ATTENDEE_TASK_ROLE_ARN", value = aws_iam_role.attendee_ecs_task.arn },
        { name = "ECS_EXECUTION_ROLE_ARN", value = aws_iam_role.ecs_task_execution.arn },
        { name = "COGNITO_CLIENT_SECRET_ARN", value = aws_secretsmanager_secret.cognito_client_secret.arn },
        { name = "ALB_LISTENER_ARN", value = aws_lb_listener.mcp_server.arn },
        { name = "ALB_DNS_NAME", value = aws_lb.mcp_server.dns_name },
        { name = "SUBNET_IDS", value = join(",", data.aws_subnets.default.ids) },
        { name = "ECS_TASKS_SECURITY_GROUP_ID", value = aws_security_group.ecs_tasks.id },
        { name = "VPC_ID", value = data.aws_vpc.default.id },
        { name = "MAX_ATTENDEE_SERVERS", value = tostring(var.max_attendee_servers) },
      ]
      secrets = [
        {
          name      = "COGNITO_CLIENT_SECRET"
          valueFrom = aws_secretsmanager_secret.cognito_client_secret.arn
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.launcher.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "mcp-launcher"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "launcher" {
  name            = "mcp-launcher"
  cluster         = aws_ecs_cluster.mcp_demo.id
  task_definition = aws_ecs_task_definition.launcher.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.launcher.arn
    container_name   = "mcp-launcher"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener_rule.launcher]
}
