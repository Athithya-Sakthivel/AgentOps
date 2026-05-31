



aws dynamodb create-table \
  --table-name agentops-rate-limits \
  --attribute-definitions AttributeName=pk,AttributeType=S \
  --key-schema AttributeName=pk,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region ap-south-1
sleep 4
aws dynamodb update-time-to-live \
  --table-name agentops-rate-limits \
  --time-to-live-specification "Enabled=true,AttributeName=ttl" \
  --region ap-south-1
