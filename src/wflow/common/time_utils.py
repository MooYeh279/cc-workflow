"""Shared time utilities — single source of truth for UTC timestamps."""

from datetime import datetime, timezone


def utc_now_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()
