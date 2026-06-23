"""
SSE broadcast bus.

How it works
────────────
Each connected SSE client registers an asyncio.Queue here.
When log_activity() commits a new SystemLog (from a sync route handler
running in uvicorn's thread-pool), it calls broadcast(), which uses
loop.call_soon_threadsafe() to safely hand the payload into the
asyncio event loop — where the SSE generator awaits on its queue.
"""
import asyncio
import json
from datetime import timezone, timedelta
from typing import Optional

from app.schemas.base import IST

_loop: Optional[asyncio.AbstractEventLoop] = None
_clients: set[asyncio.Queue] = set()


def init_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Capture the running asyncio event loop at startup."""
    global _loop
    _loop = loop


def add_client(q: asyncio.Queue) -> None:
    _clients.add(q)


def remove_client(q: asyncio.Queue) -> None:
    _clients.discard(q)


def broadcast(log_entry) -> None:
    """Push a serialised log entry to every connected SSE client.

    Safe to call from a sync thread-pool handler because it uses
    call_soon_threadsafe() rather than touching the event loop directly.
    """
    if not _loop or not _clients:
        return

    dt = log_entry.created_at
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    payload = json.dumps({
        "id":          log_entry.id,
        "action":      log_entry.action,
        "entity_type": log_entry.entity_type,
        "entity_id":   log_entry.entity_id,
        "details":     log_entry.details,
        "user":        log_entry.user,
        "created_at":  dt.isoformat() if dt else None,
    })

    for q in list(_clients):
        try:
            _loop.call_soon_threadsafe(q.put_nowait, payload)
        except Exception:
            pass
