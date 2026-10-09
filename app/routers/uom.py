from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models.models import StoreUOMOption, UOMOption, User
from app.schemas.uom import UOMOptionCreate, UOMOptionUpdate, UOMOptionOut
from app.utils.auth import require_admin
from app.utils.helpers import log_activity
from fastapi import Header

router = APIRouter(prefix="/api/uom", tags=["UOM"])


def _uom_model(uom_type: str):
    return StoreUOMOption if uom_type == "STORE" else UOMOption


@router.get("", response_model=List[UOMOptionOut])
def get_all_uom(type: str = "PO", db: Session = Depends(get_db)):
    Model = _uom_model(type)
    return db.query(Model).order_by(Model.name).all()


@router.post("", response_model=UOMOptionOut, status_code=status.HTTP_201_CREATED)
def create_uom(
    payload: UOMOptionCreate, 
    type: str = "PO",
    db: Session = Depends(get_db),
    authorization: str = Header(default=None)
):
    app_code = "store_purchase" if type.upper() == "STORE" else "marketing"
    current_user = require_admin(authorization, db, "Only admins can manage UOM options", app_code)
        
    Model = _uom_model(type)
    existing = db.query(Model).filter(Model.name.ilike(payload.name)).first()
    if existing:
        raise HTTPException(status_code=400, detail="UOM with this name already exists")

    new_uom = Model(
        name=payload.name,
        created_by=current_user.email
    )
    db.add(new_uom)
    db.commit()
    db.refresh(new_uom)
    ws = "Store" if type.upper() == "STORE" else "Marketing"
    actor = getattr(current_user, "name", None) or current_user.email
    log_activity(db, f"UOM Created ({type})", Model.__name__, f"Created UOM option '{new_uom.name}'.", actor, new_uom.id, entity_name=new_uom.name, workspace=ws)
    return new_uom


@router.put("/{uom_id}", response_model=UOMOptionOut)
def update_uom(
    uom_id: int, 
    payload: UOMOptionUpdate, 
    type: str = "PO",
    db: Session = Depends(get_db),
    authorization: str = Header(default=None)
):
    app_code = "store_purchase" if type.upper() == "STORE" else "marketing"
    current_user = require_admin(authorization, db, "Only admins can manage UOM options", app_code)
        
    Model = _uom_model(type)
    uom = db.query(Model).filter(Model.id == uom_id).first()
    if not uom:
        raise HTTPException(status_code=404, detail="UOM option not found")

    existing = db.query(Model).filter(Model.name.ilike(payload.name), Model.id != uom_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="UOM with this name already exists")

    uom.name = payload.name
    uom.updated_by = current_user.email
    from sqlalchemy.sql import func
    uom.updated_at = func.now()

    db.commit()
    db.refresh(uom)
    ws = "Store" if type.upper() == "STORE" else "Marketing"
    actor = getattr(current_user, "name", None) or current_user.email
    log_activity(db, f"UOM Updated ({type})", Model.__name__, f"Updated UOM option to '{uom.name}'.", actor, uom.id, entity_name=uom.name, workspace=ws)
    return uom


@router.delete("/{uom_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_uom(
    uom_id: int, 
    type: str = "PO",
    db: Session = Depends(get_db),
    authorization: str = Header(default=None)
):
    app_code = "store_purchase" if type.upper() == "STORE" else "marketing"
    current_user = require_admin(authorization, db, "Only admins can manage UOM options", app_code)

    Model = _uom_model(type)
    uom = db.query(Model).filter(Model.id == uom_id).first()
    if not uom:
        raise HTTPException(status_code=404, detail="UOM option not found")

    name = uom.name
    db.delete(uom)
    db.commit()
    ws = "Store" if type.upper() == "STORE" else "Marketing"
    actor = getattr(current_user, "name", None) or current_user.email
    log_activity(db, f"UOM Deleted ({type})", Model.__name__, f"Deleted UOM option '{name}'.", actor, uom_id, entity_name=name, workspace=ws)





