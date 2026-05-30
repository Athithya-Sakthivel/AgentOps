# providers.tf
# Combined provider configurations for both AWS and Cloudflare

terraform {
  required_version = ">= 1.11.6, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.40.0, < 7.0.0"
    }

    tls = {
      source  = "hashicorp/tls"
      version = ">= 4.2.1, < 5.0.0"
    }

    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = ">= 5.19.0, < 6.0.0"
    }
  }
}

###############################################################################
# AWS PROVIDER
###############################################################################

provider "aws" {
  region = var.region

  default_tags {
    tags = merge(
      {
        ManagedBy   = "opentofu"
        Environment = var.environment
      },
      var.tags
    )
  }
}

###############################################################################
# CLOUDFLARE PROVIDER
###############################################################################

provider "cloudflare" {
  # No explicit configuration needed if using environment variables:
  # CLOUDFLARE_EMAIL, CLOUDFLARE_API_KEY, or CLOUDFLARE_API_TOKEN
  # Alternatively, configure here if needed:
  # email   = var.cloudflare_email
  # api_key = var.cloudflare_api_key
  # api_token = var.cloudflare_api_token
}


