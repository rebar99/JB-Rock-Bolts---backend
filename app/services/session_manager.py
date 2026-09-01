import asyncio
from typing import Dict, Any

# Map user_id to a list of asyncio.Queue (allow multiple tabs of the same user? The requirement says 1 user, but what if they have 2 tabs open? If they have multiple tabs, they should all get the alert, and the first to respond resolves it). Let's keep it simple: map user_id to a single Queue for now, assuming 1 tab for 1 active session.
# Better: Set of queues
from collections import defaultdict

active_connections = defaultdict(list)
pending_logins = {}

async def notify_user(user_id: int, event_type: str, data: Any = None):
    queues = active_connections.get(user_id, [])
    for q in queues:
        await q.put({"type": event_type, "data": data})

