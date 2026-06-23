"""Shared Pydantic helpers.

UTCDatetime / OptUTCDatetime
    Annotated types that mark any naive datetime as IST (Asia/Kolkata, +05:30)
    before JSON serialisation.

    Why IST and not UTC
    ───────────────────
    MySQL's NOW() returns the *server local time*.  This server runs on Windows
    with the system clock set to IST, so every stored datetime is IST local time
    stored as a naive value (no tzinfo).  Labelling them UTC (+00:00) causes the
    browser to add another 5:30, displaying times 5½ hours too late.  Labelling
    them as IST (+05:30) lets any client parse the epoch correctly.
"""
from typing import Annotated, Optional
from datetime import datetime, timezone, timedelta
from pydantic.functional_serializers import PlainSerializer

# IST offset — shared by schemas AND notifications.py
IST = timezone(timedelta(hours=5, minutes=30))


def _to_ist_iso(v: Optional[datetime]) -> Optional[str]:
    """Attach IST tzinfo to a naive datetime and return ISO-8601 with +05:30 offset."""
    if v is None:
        return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=IST)
    return v.isoformat()


# Required datetime field — serialises as "2026-06-23T09:57:00+05:30"
UTCDatetime = Annotated[
    datetime,
    PlainSerializer(_to_ist_iso, return_type=str, when_used="json"),
]

# Optional datetime field — serialises as null or "2026-06-23T09:57:00+05:30"
OptUTCDatetime = Annotated[
    Optional[datetime],
    PlainSerializer(_to_ist_iso, return_type=Optional[str], when_used="json"),
]
