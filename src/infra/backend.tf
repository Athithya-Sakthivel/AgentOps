# backend.tf
terraform {
  backend "s3" {
    # No need to put values here – they will be provided via -backend-config flags
  }
}