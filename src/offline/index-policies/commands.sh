#!/usr/bin/env bash
set -euo pipefail

export AWS_REGION="${AWS_REGION:-ap-south-1}"
export S3_BUCKET="${S3_BUCKET:-agentops-embeddings-temp-xyz}"
export S3_KEY="${S3_KEY:-embeddings.json}"
export BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-amazon.titan-embed-text-v2:0}"

aws s3api head-bucket --bucket "$S3_BUCKET" --region "$AWS_REGION" 2>/dev/null || aws s3 mb "s3://$S3_BUCKET" --region "$AWS_REGION"

python3 src/offline/index-policies/index.py src/offline/index-policies --s3-bucket "$S3_BUCKET" --s3-key "$S3_KEY"

python3 src/offline/index-policies/test.py "return policy for damaged phones"
python3 src/offline/index-policies/test.py "how to cancel an order and get refund"