"""
Per-user rate limiter backed by DynamoDB.
"""

from __future__ import annotations

import logging
import time

import boto3
from botocore.exceptions import ClientError
from config import settings

log = logging.getLogger("agent-service.rate_limiter")
dynamodb = boto3.client("dynamodb", region_name=settings.aws_region)
TABLE_NAME = "agentops-rate-limits"


def _make_key(user_id: str, window: int) -> str:
    return f"rate:{user_id}:{window}"


def check_rate_limit(
    user_id: str, limit: int | None = None, window_seconds: int | None = None
) -> bool:
    """Return True if the request is allowed, False if rate limited.

    Args:
        user_id: Unique identifier for the user (e.g. `google#12345`).
        limit: Max requests per window. Defaults to config value.
        window_seconds: Duration of the window. Defaults to config value.

    """
    if not settings.rate_limit_enabled:
        return True

    limit = limit or settings.rate_limit_requests_per_minute
    window_seconds = window_seconds or settings.rate_limit_window_seconds
    current_window = int(time.time() / window_seconds)
    key = _make_key(user_id, current_window)
    ttl = int(time.time()) + window_seconds + 60  # allow a small buffer

    try:
        dynamodb.update_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": key}},
            UpdateExpression="ADD request_count :inc SET #ttl = :ttl",
            ConditionExpression="request_count < :limit OR attribute_not_exists(request_count)",
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={
                ":inc": {"N": "1"},
                ":limit": {"N": str(limit)},
                ":ttl": {"N": str(ttl)},
            },
            ReturnValues="NONE",
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            log.info("Rate limit exceeded for user %s", user_id)
            return False
        log.exception("DynamoDB rate limit error")
        # Fail open if DynamoDB is unreachable
        return True
