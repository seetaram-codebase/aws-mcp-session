output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.this.id
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.this.id
}

output "cognito_client_secret" {
  value     = aws_cognito_user_pool_client.this.client_secret
  sensitive = true
}

output "mcp_base_url" {
  value = local.mcp_base_url
}

output "hosted_ui_domain" {
  value = "${aws_cognito_user_pool_domain.this.domain}.auth.${var.aws_region}.amazoncognito.com"
}

output "test_user_email" {
  value = var.test_user_email
}

output "test_user_temporary_password" {
  value     = random_password.test_user.result
  sensitive = true
}

output "mcp_server_public_url" {
  description = "ECS-deployed server, over CloudFront's default HTTPS domain"
  value       = local.mcp_public_url
}

output "ecr_repository_url" {
  value = aws_ecr_repository.mcp_server.repository_url
}

output "launcher_public_url" {
  description = "MCP Builder Launcher -- submit.py / the Builder agent talks to this"
  value       = local.launcher_public_url
}

output "launcher_ecr_repository_url" {
  value = aws_ecr_repository.launcher.repository_url
}

output "attendee_base_ecr_repository_url" {
  value = aws_ecr_repository.attendee_base.repository_url
}

output "porkbun_dns_record_for_custom_domain" {
  description = <<-EOT
    Optional upgrade: to use mcp.seetaram.dev instead of the CloudFront
    default domain, add this CNAME at Porkbun, then separately request an
    ACM cert for mcp.seetaram.dev (DNS-validated -- Porkbun again) and add
    it as a CloudFront alternate domain name. Not automated here because
    Porkbun DNS isn't something Terraform/AWS can reach.
  EOT
  value       = "mcp.seetaram.dev  CNAME  ${aws_cloudfront_distribution.mcp_server.domain_name}"
}
