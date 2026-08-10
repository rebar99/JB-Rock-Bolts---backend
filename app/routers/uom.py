from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models.models import UOMOption, User
from app.schemas.uom import UOMOptionCreate, UOMOptionUpdate, UOMOptionOut
from app.utils.auth import require_admin
from fastapi import Header

router = APIRouter(prefix="/api/uom", tags=["UOM"])


@router.get("", response_model=List[UOMOptionOut])
def get_all_uom(db: Session = Depends(get_db)):
    return db.query(UOMOption).order_by(UOMOption.name).all()


@router.post("", response_model=UOMOptionOut, status_code=status.HTTP_201_CREATED)
def create_uom(
    payload: UOMOptionCreate, 
    db: Session = Depends(get_db),
    authorization: str = Header(default=None)
):
    current_user = require_admin(authorization, db, "Only admins can manage UOM options")
        
    existing = db.query(UOMOption).filter(UOMOption.name.ilike(payload.name)).first()
    if existing:
        raise HTTPException(status_code=400, detail="UOM with this name already exists")

    new_uom = UOMOption(
        name=payload.name,
        created_by=current_user.email
    )
    db.add(new_uom)
    db.commit()
    db.refresh(new_uom)
    return new_uom


@router.put("/{uom_id}", response_model=UOMOptionOut)
def update_uom(
    uom_id: int, 
    payload: UOMOptionUpdate, 
    db: Session = Depends(get_db),
    authorization: str = Header(default=None)
):
    current_user = require_admin(authorization, db, "Only admins can manage UOM options")
        
    uom = db.query(UOMOption).filter(UOMOption.id == uom_id).first()
    if not uom:
        raise HTTPException(status_code=404, detail="UOM option not found")

    existing = db.query(UOMOption).filter(UOMOption.name.ilike(payload.name), UOMOption.id != uom_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="UOM with this name already exists")

    uom.name = payload.name
    uom.updated_by = current_user.email
    from sqlalchemy.sql import func
    uom.updated_at = func.now()

    db.commit()
    db.refresh(uom)
    return uom


@router.delete("/{uom_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_uom(
    uom_id: int, 
    db: Session = Depends(get_db),
    authorization: str = Header(default=None)
):
    require_admin(authorization, db, "Only admins can manage UOM options")

    uom = db.query(UOMOption).filter(UOMOption.id == uom_id).first()
    if not uom:
        raise HTTPException(status_code=404, detail="UOM option not found")

    db.delete(uom)
    db.commit()
