# ----------------------------------------------------------------------
# VARIABLES (module inputs only - no environment assumptions here)
# ----------------------------------------------------------------------

variable "bucket_name" {
  type        = string
  description = "Globally unique S3 bucket name"
}

variable "force_destroy" {
  type        = bool
  description = "Allow deletion of non-empty bucket"
}

# ----------------------------------------------------------------------
# S3 BUCKET
# ----------------------------------------------------------------------

resource "aws_s3_bucket" "this" {
  # AWS provider: bucket name + force_destroy are valid arguments
  bucket        = var.bucket_name
  force_destroy = var.force_destroy

  tags = {
    Name = var.bucket_name
  }
}

# ----------------------------------------------------------------------
# PUBLIC ACCESS BLOCK (always locked down)
# ----------------------------------------------------------------------

resource "aws_s3_bucket_public_access_block" "this" {
  bucket = aws_s3_bucket.this.id

  # AWS best-practice defaults (all true = fully private bucket)
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ----------------------------------------------------------------------
# VERSIONING (safe default for embeddings / artifacts)
# ----------------------------------------------------------------------

resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id

  versioning_configuration {
    status = "Enabled"
  }
}

# ----------------------------------------------------------------------
# OUTPUTS (module-level outputs)
# ----------------------------------------------------------------------

output "bucket_name" {
  value = aws_s3_bucket.this.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.this.arn
}