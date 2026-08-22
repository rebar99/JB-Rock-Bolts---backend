from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from datetime import datetime
from app.database import get_db
from app.models.models import ItemMasterItem, ItemMasterSize, WOItemMasterItem, WOItemMasterSize
from app.schemas.item_master import ItemMasterCreate, ItemMasterUpdate, ItemMasterOut, ItemMasterSizeCreate, ItemMasterSizeOut, ItemMasterSizeUpdate
from app.utils.auth import require_admin
from app.utils.helpers import log_activity

router = APIRouter(prefix="/api/item-master", tags=["Item Master"])

ACCESS_DENIED_DETAIL = "Access Denied – Only Admin can manage items."


@router.get("", response_model=List[ItemMasterOut])
def list_items(type: str = "PO", db: Session = Depends(get_db)):
    if type == "WO":
        return (
            db.query(WOItemMasterItem)
            .options(joinedload(WOItemMasterItem.sizes))
            .order_by(WOItemMasterItem.name)
            .all()
        )
    return (
        db.query(ItemMasterItem)
        .options(joinedload(ItemMasterItem.sizes))
        .order_by(ItemMasterItem.name)
        .all()
    )


@router.post("", response_model=ItemMasterOut, status_code=status.HTTP_201_CREATED)
def create_item(
    payload: ItemMasterCreate,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, ACCESS_DENIED_DETAIL)

    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Item name is required.")

    Model = WOItemMasterItem if payload.type == "WO" else ItemMasterItem

    existing = db.query(Model).filter(func.lower(Model.name) == name.lower()).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Item '{name}' already exists.")

    item = Model(name=name, created_by=payload.created_by or "System")
    db.add(item)
    db.commit()
    db.refresh(item)

    log_activity(db, f"Item Master Item Added ({payload.type})", Model.__name__, f"Added item '{item.name}' to the Item Master.", payload.created_by or "System", item.id, entity_name=item.name)
    return item


@router.put("/{item_id}", response_model=ItemMasterOut)
def update_item(
    item_id: int,
    payload: ItemMasterUpdate,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, ACCESS_DENIED_DETAIL)

    Model = WOItemMasterItem if payload.type == "WO" else ItemMasterItem

    item = db.get(Model, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")

    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Item name is required.")

    existing = db.query(Model).filter(func.lower(Model.name) == name.lower(), Model.id != item_id).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Item '{name}' already exists.")

    old_name = item.name
    item.name = name
    item.updated_by = payload.updated_by or "System"
    item.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(item)

    log_activity(db, f"Item Master Item Updated ({payload.type})", Model.__name__, f"Renamed item '{old_name}' to '{item.name}'.", payload.updated_by or "System", item.id, entity_name=item.name)
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    item_id: int,
    type: str = "PO",
    deleted_by: Optional[str] = None,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, ACCESS_DENIED_DETAIL)

    Model = WOItemMasterItem if type == "WO" else ItemMasterItem

    item = db.get(Model, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")

    name = item.name
    db.delete(item)
    db.commit()

    log_activity(db, f"Item Master Item Deleted ({type})", Model.__name__, f"Deleted item '{name}' from the Item Master.", deleted_by or "System", entity_name=name)
    return None


# ── Sizes (per item) ─────────────────────────────────────────────────────────
# Every item can carry its own list of sizes (e.g. Couplers -> 16mm, 20mm,
# 25mm...) — same Admin-only add/delete rule as the items themselves. The PO
# Item field composes the two into one string ("Couplers 16mm") when saving,
# so no other part of the app (Reports/Overview's dia-wise parsing, etc.)
# needs to change — it already parses a trailing size out of the item text.

@router.post("/{item_id}/sizes", response_model=ItemMasterSizeOut, status_code=status.HTTP_201_CREATED)
def add_item_size(
    item_id: int,
    payload: ItemMasterSizeCreate,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, ACCESS_DENIED_DETAIL)

    Model = WOItemMasterItem if payload.type == "WO" else ItemMasterItem
    SizeModel = WOItemMasterSize if payload.type == "WO" else ItemMasterSize

    item = db.get(Model, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")

    size = (payload.size or "").strip()
    if not size:
        raise HTTPException(status_code=400, detail="Size is required.")

    existing = db.query(SizeModel).filter(
        SizeModel.item_id == item_id,
        func.lower(SizeModel.size) == size.lower(),
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Size '{size}' already exists for '{item.name}'.")

    row = SizeModel(item_id=item_id, size=size, created_by=payload.created_by or "System")
    db.add(row)
    db.commit()
    db.refresh(row)

    log_activity(db, f"Item Master Size Added ({payload.type})", SizeModel.__name__, f"Added size '{size}' to item '{item.name}'.", payload.created_by or "System", row.id, entity_name=f"{item.name} {size}")
    return row


@router.put("/{item_id}/sizes/{size_id}", response_model=ItemMasterSizeOut)
def update_item_size(
    item_id: int,
    size_id: int,
    payload: ItemMasterSizeUpdate,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, ACCESS_DENIED_DETAIL)

    Model = WOItemMasterItem if payload.type == "WO" else ItemMasterItem
    SizeModel = WOItemMasterSize if payload.type == "WO" else ItemMasterSize

    item = db.get(Model, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")

    row = db.query(SizeModel).filter(SizeModel.id == size_id, SizeModel.item_id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Size not found.")

    size = (payload.size or "").strip()
    if not size:
        raise HTTPException(status_code=400, detail="Size is required.")

    existing = db.query(SizeModel).filter(
        SizeModel.item_id == item_id,
        func.lower(SizeModel.size) == size.lower(),
        SizeModel.id != size_id,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Size '{size}' already exists for '{item.name}'.")

    old_size = row.size
    row.size = size
    db.commit()
    db.refresh(row)

    log_activity(db, f"Item Master Size Updated ({payload.type})", SizeModel.__name__, f"Updated size '{old_size}' to '{size}' for item '{item.name}'.", payload.updated_by or "System", row.id, entity_name=f"{item.name} {size}")
    return row


@router.delete("/{item_id}/sizes/{size_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item_size(
    item_id: int,
    size_id: int,
    type: str = "PO",
    deleted_by: Optional[str] = None,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, ACCESS_DENIED_DETAIL)

    SizeModel = WOItemMasterSize if type == "WO" else ItemMasterSize

    row = db.query(SizeModel).filter(SizeModel.id == size_id, SizeModel.item_id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Size not found.")

    label = f"{row.item.name} {row.size}"
    db.delete(row)
    db.commit()

    log_activity(db, f"Item Master Size Deleted ({type})", SizeModel.__name__, f"Deleted size '{label}'.", deleted_by or "System", entity_name=label)
    return None
