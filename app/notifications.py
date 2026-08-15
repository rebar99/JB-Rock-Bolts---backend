"""
SSE broadcast bus + in-memory online-user tracking.

Online presence
───────────────
When a browser opens /api/logs/stream it passes ?user_id=&user_name=&user_email=
as query params. We track a connection-count per user_id so that multiple tabs
from the same user don't drop the user from the online list when one tab closes.

Log broadcasting
────────────────
Each connected SSE client registers an asyncio.Queue here.
When log_activity() commits a new SystemLog (from a sync route handler running
in uvicorn's thread-pool) it calls broadcast(), which uses call_soon_threadsafe()
to safely hand the payload into the asyncio event loop.
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

_loop: Optional[asyncio.AbstractEventLoop] = None
_clients: set[asyncio.Queue] = set()

# user_id -> {user_id, user_name, user_email, connected_at}
_online_users: dict[int, dict] = {}
# user_id -> number of open SSE connections (multiple tabs)
_user_conn_count: dict[int, int] = {}


def init_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def add_client(q: asyncio.Queue, user_id: Optional[int] = None,
               user_name: str = "", user_email: str = "") -> None:
    _clients.add(q)
    if user_id:
        _user_conn_count[user_id] = _user_conn_count.get(user_id, 0) + 1
        if user_id not in _online_users:
            _online_users[user_id] = {
                "user_id": user_id,
                "user_name": user_name,
                "user_email": user_email,
                "connected_at": datetime.now(timezone.utc).isoformat(),
                "is_active": True,
            }


def remove_client(q: asyncio.Queue, user_id: Optional[int] = None) -> None:
    _clients.discard(q)
    if user_id:
        _user_conn_count[user_id] = max(0, _user_conn_count.get(user_id, 0) - 1)
        if _user_conn_count[user_id] == 0:
            _online_users.pop(user_id, None)
            _user_conn_count.pop(user_id, None)


def get_online_users() -> list:
    return list(_online_users.values())


def broadcast(log_entry) -> None:
    """Push a serialised log entry to every connected SSE client."""
    if not _loop or not _clients:
        return

    dt = log_entry.created_at
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    payload = json.dumps({
        "id":             log_entry.id,
        "action":         log_entry.action,
        "entity_type":    log_entry.entity_type,
        "entity_id":      log_entry.entity_id,
        "entity_name":    getattr(log_entry, "entity_name", None),
        "details":        log_entry.details,
        "changed_fields": getattr(log_entry, "changed_fields", None),
        "status":         getattr(log_entry, "status", "Success"),
        "user":           log_entry.user,
        "created_at":     dt.isoformat() if dt else None,
    })

    for q in list(_clients):
        try:
            _loop.call_soon_threadsafe(q.put_nowait, payload)
        except Exception:
            pass
