from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from app.database import get_db
from app.models.models import Record
from app.schemas.record import RecordCreate, RecordUpdate, RecordOut

router = APIRouter(prefix="/api/records", tags=["Records"])


@router.get("", response_model=List[RecordOut])
def list_records(
    client: Optional[str] = None,
    product: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    q = db.query(Record)
    if client:
        q = q.filter(Record.client_name.ilike(f"%{client}%"))
    if product:
        q = q.filter(Record.product.ilike(f"%{product}%"))
    if from_date:
        q = q.filter(Record.date >= from_date)
    if to_date:
        q = q.filter(Record.date <= to_date)
    return q.order_by(Record.date.desc()).offset(skip).limit(limit).all()


@router.post("", response_model=RecordOut, status_code=status.HTTP_201_CREATED)
def create_record(payload: RecordCreate, db: Session = Depends(get_db)):
    record = Record(**payload.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.put("/{record_id}", response_model=RecordOut)
def update_record(record_id: int, payload: RecordUpdate, db: Session = Depends(get_db)):
    record = db.get(Record, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found.")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(record, field, value)
    db.commit()
    db.refresh(record)
    return record


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_record(record_id: int, db: Session = Depends(get_db)):
    record = db.get(Record, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found.")
    db.delete(record)
    db.commit()
