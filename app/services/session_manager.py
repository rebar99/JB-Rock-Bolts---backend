import asyncio
from typing import Dict, Any, List
from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        # user_id -> list of WebSockets
        self.active_connections: Dict[int, List[WebSocket]] = {}
        # request_id -> asyncio.Event
        self.pending_login_events: Dict[str, asyncio.Event] = {}
        # request_id -> result (True/False)
        self.pending_login_results: Dict[str, bool] = {}

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)

    def disconnect(self, websocket: WebSocket, user_id: int):
        if user_id in self.active_connections:
            if websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

    def is_user_active(self, user_id: int) -> bool:
        return user_id in self.active_connections and len(self.active_connections[user_id]) > 0

    async def notify_user(self, user_id: int, message: dict):
        if user_id in self.active_connections:
            for connection in self.active_connections[user_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    pass

manager = ConnectionManager()
