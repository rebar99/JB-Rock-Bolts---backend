"""
SSE broadcast bus + in-memory online-user tracking.

Online presence
───────────────
When a browser opens /api/logs/stream it passes ?user_id=&user_name=&user_email=&workspace=
as query params. We track a connection-count per user_id so that multiple tabs
from the same user don't drop the user from the online list when one tab closes.

Log broadcasting
────────────────
Each connected SSE client registers an asyncio.Queue here.
When log_activity() commits a new SystemLog (from a sync route handler running
in uvicorn's thread-pool) it calls broadcast(), which uses call_soon_threadsafe()
to safely hand the payload into the asyncio event loop.

Workspace filtering
───────────────────
Each client queue is tagged with the workspace the browser is connected to
(e.g. "Marketing" or "Store").  broadcast() only pushes to clients whose
workspace matches the log entry's workspace, so Marketing users never see
Store notifications and vice versa.
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

_loop: Optional[asyncio.AbstractEventLoop] = None

# Maps each queue to the workspace it belongs to
_clients: dict[asyncio.Queue, str] = {}

# user_id -> {user_id, user_name, user_email, workspace, connected_at}
_online_users: dict[int, dict] = {}
# user_id -> number of open SSE connections (multiple tabs)
_user_conn_count: dict[int, int] = {}


def init_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def _normalize_workspace(ws: str) -> str:
    """Normalize workspace label so frontend and backend names always match.

    Frontend sends: "Marketing" | "Store Purchase" | "Software Selection"
    Backend sends:  "Marketing" | "Store"

    Both "Store Purchase" and "Store" map to the same "Store" bucket.
    """
    if not ws:
        return "Marketing"
    ws = ws.strip()
    if ws.lower().startswith("store"):
        return "Store"
    if ws.lower() == "marketing":
        return "Marketing"
    return ws  # Other workspaces (e.g. "Software Selection") pass through


def add_client(q: asyncio.Queue, user_id: Optional[int] = None,
               user_name: str = "", user_email: str = "",
               workspace: str = "") -> None:
    _clients[q] = _normalize_workspace(workspace)
    if user_id:
        _user_conn_count[user_id] = _user_conn_count.get(user_id, 0) + 1
        prev_connected = _online_users.get(user_id, {}).get("connected_at")
        _online_users[user_id] = {
            "user_id": user_id,
            "user_name": user_name,
            "user_email": user_email,
            "workspace": workspace or _online_users.get(user_id, {}).get("workspace", "Marketing"),
            "connected_at": prev_connected or datetime.now(timezone.utc).isoformat(),
            "is_active": True,
        }


def remove_client(q: asyncio.Queue, user_id: Optional[int] = None) -> None:
    _clients.pop(q, None)
    if user_id:
        _user_conn_count[user_id] = max(0, _user_conn_count.get(user_id, 0) - 1)
        if _user_conn_count[user_id] == 0:
            _online_users.pop(user_id, None)
            _user_conn_count.pop(user_id, None)


def remove_user(user_id: int) -> None:
    """Explicitly remove a user from the in-memory online users list (used on logout/force-logout)."""
    _online_users.pop(user_id, None)
    _user_conn_count.pop(user_id, None)


def get_online_users() -> list:
    return list(_online_users.values())


def broadcast(log_entry) -> None:
    """Push a serialised log entry only to SSE clients in the same workspace."""
    if not _loop or not _clients:
        return

    dt = log_entry.created_at
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    # Workspace the log came from (defaults to "Marketing" for legacy entries)
    log_workspace = _normalize_workspace(getattr(log_entry, "workspace", None) or "Marketing")

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
        "workspace":      log_workspace,
        "created_at":     dt.isoformat() if dt else None,
    })

    for q, client_workspace in list(_clients.items()):
        # Only send to clients in the same workspace
        if client_workspace == log_workspace:
            try:
                _loop.call_soon_threadsafe(q.put_nowait, payload)
            except Exception:
                pass
