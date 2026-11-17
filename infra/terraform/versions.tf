terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  backend "s3" {
    # bucket/key/region are supplied via -backend-config in CI
    # (infra/bootstrap_github_oidc.py prints the bucket name).
    use_lockfile = true # S3-native locking (TF >= 1.10) -- no DynamoDB table needed
  }
}

provider "aws" {
  region = var.aws_region
}
