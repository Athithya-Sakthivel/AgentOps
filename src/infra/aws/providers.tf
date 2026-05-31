# providers.tf
# Combined provider configurations for AWS

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
  }
}

###############################################################################
# VARIABLES
###############################################################################

variable "region" {
  description = "AWS region for resource deployment"
  type        = string
  default     = "ap-south-1"
}

variable "environment" {
  description = "Environment name (e.g., staging, production)"
  type        = string
  default     = "staging"
}

variable "name_prefix" {
  description = "Prefix for resource naming"
  type        = string
  default     = "agentops"
}

variable "tags" {
  description = "Additional tags to apply to all resources"
  type        = map(string)
  default     = {}
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