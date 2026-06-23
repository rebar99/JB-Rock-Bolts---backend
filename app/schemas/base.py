"""Shared Pydantic helpers.

UTCDatetime / OptUTCDatetime
    Annotated types that mark any naive datetime as UTC (+00:00)
    before JSON serialisation.

    Railway's MySQL server runs in UTC, so MySQL NOW() stores UTC time as
    a naive value (no tzinfo). We label them UTC so the browser can parse
    the correct epoch and convert to the user's local timezone.
"""
from typing import Annotated, Optional
from datetime import datetime, timezone, timedelta
from pydantic.functional_serializers import PlainSerializer

# IST offset — kept for backwards compat import in notifications.py
IST = timezone(timedelta(hours=5, minutes=30))


def _to_utc_iso(v: Optional[datetime]) -> Optional[str]:
    """Attach UTC tzinfo to a naive datetime and return ISO-8601 with +00:00 offset."""
    if v is None:
        return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.isoformat()


# Required datetime field — serialises as "2026-06-23T09:57:00+00:00"
UTCDatetime = Annotated[
    datetime,
    PlainSerializer(_to_utc_iso, return_type=str, when_used="json"),
]

# Optional datetime field — serialises as null or "2026-06-23T09:57:00+00:00"
OptUTCDatetime = Annotated[
    Optional[datetime],
    PlainSerializer(_to_utc_iso, return_type=Optional[str], when_used="json"),
]
