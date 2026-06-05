from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models.models import SystemLog
from app.schemas.log import SystemLogOut

router = APIRouter(prefix="/api/logs", tags=["Logs"])

@router.get("", response_model=List[SystemLogOut])
def list_logs(limit: int = 50, db: Session = Depends(get_db)):
    """Fetch the most recent system logs."""
    return db.query(SystemLog).order_by(SystemLog.created_at.desc()).limit(limit).all()
