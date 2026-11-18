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

variable "launcher_image_tag" {
  type        = string
  description = "Tag of the mcp-launcher image in ECR to deploy"
  default     = "latest"
}

variable "attendee_base_image_tag" {
  type        = string
  description = "Tag of the mcp-attendee-base image in ECR -- the one shared image every attendee-submitted server runs, parameterized by the ATTENDEE_TOOL_CODE env var"
  default     = "latest"
}

variable "max_attendee_servers" {
  type        = number
  description = "Cap on concurrently active attendee servers the Launcher will provision -- a guardrail against one runaway submit loop spinning up unlimited infrastructure"
  default     = 20
}
