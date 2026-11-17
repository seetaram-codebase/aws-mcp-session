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
  value = var.workshop ? null : var.test_user_email
}

output "test_user_temporary_password" {
  value     = var.workshop ? null : random_password.test_user[0].result
  sensitive = true
}
