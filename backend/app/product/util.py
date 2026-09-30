"""Small shared helpers for the product read models."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def iso(dt: datetime | None) -> str | None:
    """ISO-8601 with an explicit UTC offset (SQLite hands back naive UTC datetimes)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def loads(raw: str | None) -> Any:
    """Parse stored JSON text; None/invalid → None."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None
