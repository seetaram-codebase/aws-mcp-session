# One shared Cognito User Pool, used by both server/finance_mcp_server.py
# (speaker's stage demo) and kickstart/server_template.py (what attendees
# clone for the live hands-on segment). Previously these were two separate
# pools ("solo" vs "workshop") -- consolidated to one, since the only real
# differences (admin test user vs. self-signup) aren't mutually exclusive:
# self-signup stays open for attendees AND one admin-created user exists
# for the speaker's own rehearsed run, in the same pool.
#
# NOTE: resource names below (pool_name, resource_id, etc.) are kept
# identical to the former "workshop" mode on purpose -- this pool already
# exists in AWS under this shape, and changing names here would make
# Terraform try to recreate instead of reconcile.

locals {
  pool_name   = "mcp-workshop-pool"
  resource_id = "mcp"
  scope_name  = "access"
  scope_desc  = "Access MCP workshop tools"
  full_scope  = "${local.resource_id}/${local.scope_name}"
  client_name = "mcp-workshop-client"

  callback_urls = [
    "http://localhost:8000/auth/callback",
    "http://127.0.0.1:8000/auth/callback",
    # ECS-deployed server, fronted by CloudFront for HTTPS -- see ecs.tf.
    "https://${aws_cloudfront_distribution.mcp_server.domain_name}/auth/callback",
    # MCP Builder Launcher, fronted by its own CloudFront -- see launcher.tf.
    # NOTE: launcher.tf's submit_server tool appends further callback URLs
    # to this same app client at runtime (one per attendee server it
    # provisions) -- Terraform does not own those, only these fixed ones,
    # so expect `terraform plan` to show perpetual drift here once
    # attendees start submitting. That's expected, not a bug.
    "https://${aws_cloudfront_distribution.launcher.domain_name}/auth/callback",
  ]

  mcp_base_url = "http://localhost:8000"
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

  # Explicit, not just relying on the default: self-service sign-up must
  # stay open so attendees can create their own Hosted UI login.
  admin_create_user_config {
    allow_admin_create_user_only = false
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = "${local.pool_name}-${random_id.domain_suffix.hex}"
  user_pool_id = aws_cognito_user_pool.this.id
}

resource "aws_cognito_resource_server" "this" {
  identifier   = local.resource_id
  name         = "MCP Workshop API"
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
  length  = 16
  special = false
}

# Speaker's own login for the stage demo -- self-signup stays open above
# for everyone else, this just means you don't have to click through it
# yourself before going on stage.
#
# NOTE: aws_cognito_user only sets a *temporary* password -- there's no
# Terraform-native equivalent of admin_set_user_password to make it
# permanent (that's a separate, non-idempotent API call, which is why
# infra/setup_cognito.py -- the boto3 version -- does it as an explicit
# extra step). First Hosted UI login with this password will prompt for a
# permanent one; set it once before you're on stage, not live.
resource "aws_cognito_user" "test_user" {
  user_pool_id = aws_cognito_user_pool.this.id
  username     = var.test_user_email

  attributes = {
    email          = var.test_user_email
    email_verified = true
  }

  message_action     = "SUPPRESS"
  temporary_password = random_password.test_user.result
}
