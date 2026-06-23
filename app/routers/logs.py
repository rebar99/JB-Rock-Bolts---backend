import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models.models import SystemLog
from app.schemas.log import SystemLogOut
from app import notifications

router = APIRouter(prefix="/api/logs", tags=["Logs"])


@router.get("", response_model=List[SystemLogOut])
def list_logs(limit: int = 50, db: Session = Depends(get_db)):
    """Fetch the most recent system logs."""
    return db.query(SystemLog).order_by(SystemLog.created_at.desc()).limit(limit).all()


@router.get("/stream")
async def stream_logs(request: Request):
    """SSE endpoint — pushes new SystemLog entries to the browser in real time.

    The browser's EventSource reconnects automatically if the connection drops.
    A ': heartbeat' comment is sent every 25 seconds to keep the connection
    alive through proxies and load-balancers that close idle connections.
    """
    queue: asyncio.Queue = asyncio.Queue()
    notifications.add_client(queue)

    async def event_gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive comment — EventSource ignores lines starting with ':'
                    yield ": heartbeat\n\n"
        finally:
            notifications.remove_client(queue)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx/reverse-proxy buffering
            "Connection": "keep-alive",
        },
    )
