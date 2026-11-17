variable "aws_region" {
  type        = string
  description = "AWS region to provision Cognito into"
}

variable "workshop" {
  type        = bool
  description = <<-EOT
    true  = shared pool for the live kickstart segment: self-service sign-up
            enabled, no test user, generic "mcp/access" scope, localhost
            callback URLs for every attendee's own laptop.
    false = solo pool for the speaker's own finance_mcp_server.py reference
            demo: one admin-created test user, "finance/read" scope.
  EOT
  default     = false
}

variable "test_user_email" {
  type        = string
  description = "Email for the solo-demo test user (ignored when workshop = true)"
  default     = "demo@example.com"
}

variable "callback_url" {
  type        = string
  description = "OAuth callback URL for the solo demo (ignored when workshop = true, which always uses localhost:8000)"
  default     = "http://localhost:8000/auth/callback"
}
