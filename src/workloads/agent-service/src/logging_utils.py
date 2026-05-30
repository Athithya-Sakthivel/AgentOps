"""
Structured JSON logging with correlation ID (run_id).

All logs are written to stderr as JSON Lines, ready for CloudWatch.
No OpenTelemetry.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any


def log_event(
    level: str,
    message: str,
    run_id: str = "",
    **kwargs: Any,
) -> None:
    """Emit a structured log line."""
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": level,
        "message": message,
        "run_id": run_id,
        **kwargs,
    }
    print(json.dumps(payload, default=str), file=sys.stderr)
