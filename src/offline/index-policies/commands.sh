#!/bin/bash
set -euo pipefail

export AWS_REGION="${TF_VAR_region:-ap-south-1}"
export S3_BUCKET=$(tofu -chdir=src/infra/aws output -json | jq -r '.s3_bucket.value.name')
export S3_KEY="${S3_KEY:-embeddings.json}"
export BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-amazon.titan-embed-text-v2:0}"

# Run the indexing script
python3 src/offline/index-policies/index.py src/offline/index-policies \
    --s3-bucket "$S3_BUCKET" \
    --s3-key "$S3_KEY"

