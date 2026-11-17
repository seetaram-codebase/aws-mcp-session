variable "aws_region" {
  type        = string
  description = "AWS region to provision Cognito into"
}

variable "test_user_email" {
  type        = string
  description = "Email for the speaker's own admin-created test user (everyone else self-signs-up)"
  default     = "demo@example.com"
}
