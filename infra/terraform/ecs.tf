# ECS Fargate deployment of server/finance_mcp_server.py, behind an ALB,
# fronted by CloudFront for free HTTPS on the default *.cloudfront.net
# domain -- Cognito requires HTTPS callback URLs for anything other than
# localhost, and getting our own ACM cert would mean blocking `apply` on
# manual DNS validation at Porkbun (where seetaram.dev's DNS lives, not
# Route53 -- Terraform can't create those records for us). CloudFront's
# default domain sidesteps that entirely: automatable end to end today.
# A custom mcp.seetaram.dev domain can be layered on later (see outputs.tf
# for the DNS records that would need adding at Porkbun).

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

# ---------------------------------------------------------------------------
# ECR
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "mcp_server" {
  name                 = "mcp-finance-server"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
}

# ---------------------------------------------------------------------------
# Secrets Manager -- the Cognito app client secret, read by the ECS task
# execution role at launch (never baked into the image or task def as
# plaintext). Value comes straight from the Cognito client Terraform
# already manages above.
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "cognito_client_secret" {
  name                    = "mcp-server/cognito-client-secret"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "cognito_client_secret" {
  secret_id     = aws_secretsmanager_secret.cognito_client_secret.id
  secret_string = aws_cognito_user_pool_client.this.client_secret
}

# ---------------------------------------------------------------------------
# IAM -- two roles, deliberately different shapes:
#   - execution role: what ECS itself needs (pull from ECR, write logs,
#     read the one secret above). This is infra plumbing, not the app.
#   - task role: what the RUNNING CONTAINER can do as itself. This demo's
#     tools are all in-memory mock data -- it calls no AWS APIs -- so the
#     task role is intentionally permission-less. That's the "least
#     privilege taken to its logical conclusion" version of the IAM task
#     role story from session-outline section 04/05.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  name               = "mcp-server-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_task_execution_secret" {
  name = "read-cognito-client-secret"
  role = aws_iam_role.ecs_task_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "secretsmanager:GetSecretValue"
      Resource = aws_secretsmanager_secret.cognito_client_secret.arn
    }]
  })
}

resource "aws_iam_role" "ecs_task" {
  name               = "mcp-server-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  # No policies attached -- see comment above. The app itself touches no
  # AWS APIs, so its runtime role grants nothing.
}

# ---------------------------------------------------------------------------
# Networking -- default VPC's public subnets, no NAT needed (tasks get a
# public IP directly). ALB only accepts traffic from CloudFront's IP
# range (AWS-managed prefix list), not the open internet -- the only path
# in is through CloudFront.
# ---------------------------------------------------------------------------

resource "aws_security_group" "alb" {
  name   = "mcp-server-alb"
  vpc_id = data.aws_vpc.default.id

  ingress {
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "ecs_tasks" {
  name   = "mcp-server-ecs-tasks"
  vpc_id = data.aws_vpc.default.id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ---------------------------------------------------------------------------
# ALB (HTTP only -- CloudFront terminates TLS for viewers; the ALB<->
# CloudFront leg rides the AWS backbone)
# ---------------------------------------------------------------------------

resource "aws_lb" "mcp_server" {
  name               = "mcp-finance-server"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = data.aws_subnets.default.ids
}

resource "aws_lb_target_group" "mcp_server" {
  name        = "mcp-finance-server"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.default.id
  target_type = "ip"

  health_check {
    path = "/mcp"
    # 401 counts as healthy here: it means the process is up and the
    # OAuth middleware is correctly rejecting an unauthenticated health
    # probe -- there's no unauthenticated route to check against.
    matcher = "200,401"
  }
}

resource "aws_lb_listener" "mcp_server" {
  load_balancer_arn = aws_lb.mcp_server.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.mcp_server.arn
  }
}

# ---------------------------------------------------------------------------
# CloudFront -- free HTTPS on the default domain, no ACM/DNS needed
# ---------------------------------------------------------------------------

resource "aws_cloudfront_distribution" "mcp_server" {
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

    # AWS-managed "CachingDisabled" + "AllViewer" -- this is a dynamic API,
    # not static content, and MCP needs the Authorization header plus all
    # query strings forwarded untouched.
    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    origin_request_policy_id = "216adef6-5c7f-47e4-b989-5492eafa07d3"
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

# ---------------------------------------------------------------------------
# ECS cluster, task definition, service
# ---------------------------------------------------------------------------

resource "aws_ecs_cluster" "mcp_demo" {
  name = "mcp-demo-cluster"
}

resource "aws_cloudwatch_log_group" "mcp_server" {
  name              = "/ecs/mcp-finance-server"
  retention_in_days = 7
}

locals {
  mcp_public_url = "https://${aws_cloudfront_distribution.mcp_server.domain_name}"
}

resource "aws_ecs_task_definition" "mcp_server" {
  family                   = "mcp-finance-server"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name         = "mcp-finance-server"
      image        = "${aws_ecr_repository.mcp_server.repository_url}:${var.image_tag}"
      essential    = true
      portMappings = [{ containerPort = 8000, protocol = "tcp" }]
      environment = [
        { name = "COGNITO_USER_POOL_ID", value = aws_cognito_user_pool.this.id },
        { name = "COGNITO_CLIENT_ID", value = aws_cognito_user_pool_client.this.id },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "MCP_BASE_URL", value = local.mcp_public_url },
        { name = "PORT", value = "8000" },
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
          "awslogs-group"         = aws_cloudwatch_log_group.mcp_server.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "mcp-server"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "mcp_server" {
  name            = "mcp-finance-server"
  cluster         = aws_ecs_cluster.mcp_demo.id
  task_definition = aws_ecs_task_definition.mcp_server.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.mcp_server.arn
    container_name   = "mcp-finance-server"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.mcp_server]
}
