variable "aws_region" {
  type        = string
  description = "AWS region to provision Cognito into"
}

variable "test_user_email" {
  type        = string
  description = "Email for the speaker's own admin-created test user (everyone else self-signs-up)"
  default     = "demo@example.com"
}

variable "image_tag" {
  type        = string
  description = "Tag of the mcp-finance-server image in ECR to deploy (pushed by .github/workflows/deploy-mcp-ecs.yml)"
  default     = "latest"
}
