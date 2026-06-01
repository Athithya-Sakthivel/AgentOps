#!/usr/bin/env bash
# alerts.sh – Set up SNS topic and subscription for CloudWatch alarms

set -euo pipefail

# Configuration
REGION="${AWS_DEFAULT_REGION:-ap-south-1}"
TOPIC_NAME="${TOPIC_NAME:-agentops-staging-alarms}"
EMAIL="${EMAIL:-athithya651@gmail.com}"

echo "Setting up SNS topic and subscription for CloudWatch alarms..."

# Create SNS topic (ignore if already exists)
TOPIC_ARN=$(aws sns create-topic --name "$TOPIC_NAME" --region "$REGION" --query 'TopicArn' --output text 2>/dev/null || echo "")
if [ -z "$TOPIC_ARN" ]; then
  echo "Failed to create topic. It may already exist; fetching ARN..."
  TOPIC_ARN=$(aws sns list-topics --region "$REGION" --query "Topics[?contains(TopicArn, '$TOPIC_NAME')].TopicArn" --output text)
fi

echo "SNS Topic ARN: $TOPIC_ARN"

# Subscribe email (idempotent)
SUBSCRIPTION_ARN=$(aws sns subscribe \
  --topic-arn "$TOPIC_ARN" \
  --protocol email \
  --notification-endpoint "$EMAIL" \
  --region "$REGION" \
  --query 'SubscriptionArn' --output text 2>/dev/null || echo "")
echo "Subscription requested to $EMAIL. Please check your inbox and click the confirmation link."

# Export the variable for Terraform
export TF_VAR_alarm_sns_topic_arn="$TOPIC_ARN"
echo "Exported TF_VAR_alarm_sns_topic_arn=$TOPIC_ARN"

# Optional: verify subscription status (run after confirmation)
echo ""
echo "To verify subscription status later, run:"
echo "  aws sns list-subscriptions-by-topic --topic-arn \"$TOPIC_ARN\" --region $REGION"