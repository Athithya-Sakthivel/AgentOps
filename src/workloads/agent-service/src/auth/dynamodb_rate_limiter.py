# auth/dynamodb_rate_limiter.py — Final
"""
Per-user rate limiter backed by DynamoDB.
Fails open — if DynamoDB is unreachable, requests are allowed.
"""

from __future__ import annotations

import logging
import time

import boto3
from botocore.exceptions import ClientError
from config import settings

log = logging.getLogger("agent-service.rate_limiter")
dynamodb = boto3.client("dynamodb", region_name=settings.aws_region)


def check_rate_limit(
    user_id: str,
    limit: int | None = None,
    window_seconds: int | None = None,
) -> bool:
    """Return True if the request is allowed, False if rate limited."""
    if not settings.rate_limit_enabled:
        return True

    limit = limit or settings.rate_limit_requests_per_minute
    window_seconds = window_seconds or settings.rate_limit_window_seconds
    current_window = int(time.time() / window_seconds)
    ttl = int(time.time()) + window_seconds + 60

    try:
        dynamodb.update_item(
            TableName=settings.dynamodb_table_name,
            Key={
                settings.dynamodb_hash_key: {"S": f"rate:{user_id}"},
                settings.dynamodb_range_key: {"S": str(current_window)},
            },
            UpdateExpression="ADD request_count :inc SET #ttl = :ttl",
            ConditionExpression="request_count < :limit OR attribute_not_exists(request_count)",
            ExpressionAttributeNames={"#ttl": settings.dynamodb_ttl_attribute},
            ExpressionAttributeValues={
                ":inc": {"N": "1"},
                ":limit": {"N": str(limit)},
                ":ttl": {"N": str(ttl)},
            },
        )
        return True
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ConditionalCheckFailedException":
            log.info("Rate limit exceeded for user %s", user_id)
            return False
        log.warning("DynamoDB rate limit error (failing open): %s", code)
        return True
    except Exception:
        log.exception("DynamoDB rate limit unexpected error (failing open)")
        return True
