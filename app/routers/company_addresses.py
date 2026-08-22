from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models.models import CompanyAddress
from app.schemas.company_address import CompanyAddressCreate, CompanyAddressUpdate, CompanyAddressOut
from app.utils.auth import require_admin
from app.utils.helpers import log_activity

router = APIRouter(prefix="/api/company-addresses", tags=["Company Addresses"])


@router.get("", response_model=List[CompanyAddressOut])
def list_addresses(db: Session = Depends(get_db)):
    return db.query(CompanyAddress).order_by(CompanyAddress.created_at).all()


@router.post("", response_model=CompanyAddressOut, status_code=status.HTTP_201_CREATED)
def create_address(
    payload: CompanyAddressCreate,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db)
):
    user = require_admin(authorization, db, detail="Only admins can manage company addresses.")
    
    # If this is the first address or set as default, handle defaults
    if payload.is_default:
        db.query(CompanyAddress).update({CompanyAddress.is_default: False})
        
    address = CompanyAddress(
        title=payload.title,
        address_text=payload.address_text,
        is_default=payload.is_default
    )
    db.add(address)
    db.commit()
    db.refresh(address)

    # Ensure there is at least one default if this is the only address
    if not address.is_default and db.query(CompanyAddress).count() == 1:
        address.is_default = True
        db.commit()
        db.refresh(address)

    log_activity(db, "Address Created", "CompanyAddress", f"Added company address: {address.title}", user.name, address.id, entity_name=address.title)
    return address


@router.put("/{address_id}", response_model=CompanyAddressOut)
def update_address(
    address_id: int,
    payload: CompanyAddressUpdate,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db)
):
    user = require_admin(authorization, db, detail="Only admins can manage company addresses.")
    address = db.get(CompanyAddress, address_id)
    if not address:
        raise HTTPException(status_code=404, detail="Company address not found.")

    if payload.is_default:
        db.query(CompanyAddress).update({CompanyAddress.is_default: False})

    if payload.title is not None:
        address.title = payload.title
    if payload.address_text is not None:
        address.address_text = payload.address_text
    if payload.is_default is not None:
        address.is_default = payload.is_default

    db.commit()
    db.refresh(address)
    
    log_activity(db, "Address Updated", "CompanyAddress", f"Updated company address: {address.title}", user.name, address.id, entity_name=address.title)
    return address


@router.delete("/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_address(
    address_id: int,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db)
):
    user = require_admin(authorization, db, detail="Only admins can manage company addresses.")
    address = db.get(CompanyAddress, address_id)
    if not address:
        raise HTTPException(status_code=404, detail="Company address not found.")

    title = address.title
    db.delete(address)
    db.commit()
    
    # If we deleted the default, set another one as default if it exists
    if address.is_default:
        other = db.query(CompanyAddress).first()
        if other:
            other.is_default = True
            db.commit()

    log_activity(db, "Address Deleted", "CompanyAddress", f"Deleted company address: {title}", user.name, address_id, entity_name=title)
    return None


@router.post("/{address_id}/set-default", response_model=CompanyAddressOut)
def set_default_address(
    address_id: int,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db)
):
    user = require_admin(authorization, db, detail="Only admins can manage company addresses.")
    address = db.get(CompanyAddress, address_id)
    if not address:
        raise HTTPException(status_code=404, detail="Company address not found.")

    db.query(CompanyAddress).update({CompanyAddress.is_default: False})
    address.is_default = True
    db.commit()
    db.refresh(address)
    
    log_activity(db, "Address Set Default", "CompanyAddress", f"Set company address {address.title} as default", user.name, address.id, entity_name=address.title)
    return address
