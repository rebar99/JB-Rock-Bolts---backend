import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import SystemLog
from app.schemas.log import SystemLogOut
from app import notifications

router = APIRouter(prefix="/api/logs", tags=["Logs"])


@router.get("", response_model=List[SystemLogOut])
def list_logs(limit: int = 100, db: Session = Depends(get_db)):
    return db.query(SystemLog).order_by(SystemLog.created_at.desc()).limit(limit).all()


@router.get("/online-users")
def get_online_users():
    """Return users who currently have an active SSE connection (i.e. the app is open)."""
    return notifications.get_online_users()


@router.get("/stream")
async def stream_logs(
    request: Request,
    user_id: Optional[int] = None,
    user_name: Optional[str] = None,
    user_email: Optional[str] = None,
):
    """SSE endpoint — pushes new SystemLog entries to the browser in real time.

    Accepts optional user_id / user_name / user_email query params so the server
    can track which users currently have the app open (online presence).
    """
    queue: asyncio.Queue = asyncio.Queue()
    notifications.add_client(queue, user_id, user_name or "", user_email or "")

    async def event_gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            notifications.remove_client(queue, user_id)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
