# Terraform port of infra/setup_cognito.py. Same two modes (solo demo vs.
# shared workshop pool), same resource shape -- see that script's docstring
# for the full rationale. This is the version GitHub Actions runs; the
# Python script remains for anyone who'd rather provision locally without
# touching Terraform state.

locals {
  pool_name   = var.workshop ? "mcp-workshop-pool" : "mcp-demo-finance-pool"
  resource_id = var.workshop ? "mcp" : "finance"
  scope_name  = var.workshop ? "access" : "read"
  scope_desc  = var.workshop ? "Access MCP workshop tools" : "Read portfolio and quote data"
  full_scope  = "${local.resource_id}/${local.scope_name}"
  client_name = var.workshop ? "mcp-workshop-client" : "mcp-demo-finance-client"

  callback_urls = var.workshop ? [
    "http://localhost:8000/auth/callback",
    "http://127.0.0.1:8000/auth/callback",
  ] : [var.callback_url]

  mcp_base_url = var.workshop ? "http://localhost:8000" : trimsuffix(var.callback_url, "/auth/callback")
}

resource "random_id" "domain_suffix" {
  byte_length = 4
}

resource "aws_cognito_user_pool" "this" {
  name                     = local.pool_name
  auto_verified_attributes = ["email"]
  username_attributes      = ["email"]

  password_policy {
    minimum_length    = 8
    require_uppercase = false
    require_lowercase = false
    require_numbers   = false
    require_symbols   = false
  }

  # Explicit, not just relying on the default: self-service sign-up must stay
  # open in workshop mode so attendees can create their own Hosted UI login.
  admin_create_user_config {
    allow_admin_create_user_only = var.workshop ? false : true
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = "${local.pool_name}-${random_id.domain_suffix.hex}"
  user_pool_id = aws_cognito_user_pool.this.id
}

resource "aws_cognito_resource_server" "this" {
  identifier   = local.resource_id
  name         = var.workshop ? "MCP Workshop API" : "Finance MCP API"
  user_pool_id = aws_cognito_user_pool.this.id

  scope {
    scope_name        = local.scope_name
    scope_description = local.scope_desc
  }
}

resource "aws_cognito_user_pool_client" "this" {
  name         = local.client_name
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret                      = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", local.full_scope]
  callback_urls                        = local.callback_urls
  supported_identity_providers         = ["COGNITO"]
  explicit_auth_flows                  = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]

  depends_on = [aws_cognito_resource_server.this]
}

resource "random_password" "test_user" {
  count   = var.workshop ? 0 : 1
  length  = 16
  special = false
}

# Solo demo only -- workshop mode relies on attendee self-signup instead.
#
# NOTE: the aws_cognito_user resource only sets a *temporary* password --
# there's no Terraform-native equivalent of admin_set_user_password to make
# it permanent (that's a separate, non-idempotent API call, which is why
# infra/setup_cognito.py -- the boto3 version -- does it as an explicit
# extra step). First Hosted UI login with this password will prompt for a
# permanent one; set it once before you're on stage.
resource "aws_cognito_user" "test_user" {
  count        = var.workshop ? 0 : 1
  user_pool_id = aws_cognito_user_pool.this.id
  username     = var.test_user_email

  attributes = {
    email          = var.test_user_email
    email_verified = true
  }

  message_action     = "SUPPRESS"
  temporary_password = random_password.test_user[0].result
}
