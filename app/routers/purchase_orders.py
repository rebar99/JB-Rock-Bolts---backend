from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
from datetime import datetime
import os
import uuid
from app.database import get_db
from app.models.models import PurchaseOrder, POLineItem
from app.schemas.purchase_order import PurchaseOrderCreate, PurchaseOrderUpdate, PurchaseOrderOut
from app.utils.helpers import log_activity
from app.services.storage import save_upload

router = APIRouter(prefix="/api/purchase-orders", tags=["Purchase Orders"])


@router.post("/upload")
async def upload_po_file(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename)[1]
    filename = f"{uuid.uuid4()}{ext}"
    file_bytes = await file.read()
    file_url = save_upload(file_bytes, filename, file.content_type or "application/octet-stream")
    return {"file_url": file_url}


@router.get("", response_model=List[PurchaseOrderOut])
def list_purchase_orders(
    search: Optional[str] = None,
    client: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    db.expire_all()
    q = db.query(PurchaseOrder)
    if search:
        q = q.filter(
            (PurchaseOrder.po_number.ilike(f"%{search}%")) |
            (PurchaseOrder.client_name.ilike(f"%{search}%"))
        )
    if client:
        q = q.filter(PurchaseOrder.client_name.ilike(f"%{client}%"))
    
    return q.order_by(PurchaseOrder.created_at.desc()).offset(skip).limit(limit).all()


@router.post("", response_model=PurchaseOrderOut, status_code=status.HTTP_201_CREATED)
def create_purchase_order(payload: PurchaseOrderCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"line_items"})

    # If line_items provided, set legacy fields from first item for backward compat
    if payload.line_items:
        first = payload.line_items[0]
        data["item"] = first.item
        data["uom"] = first.uom
        data["unit_price"] = first.unit_price
        data["total_quantity"] = sum(li.quantity for li in payload.line_items)

    po = PurchaseOrder(**data)

    # Create line items
    for li_data in payload.line_items:
        po.line_items.append(POLineItem(**li_data.model_dump()))

    db.add(po)
    try:
        db.commit()
        db.refresh(po)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Purchase order with PO# '{payload.po_number}' already exists.",
        )
        
    log_activity(db, "PO Created", "PurchaseOrder", f"Created PO {po.po_number} for {po.client_name}.", payload.created_by or "System", po.id)
    return po


@router.get("/{po_id}", response_model=PurchaseOrderOut)
def get_purchase_order(po_id: int, opened_by: Optional[str] = None, db: Session = Depends(get_db)):
    po = db.get(PurchaseOrder, po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found.")
    if opened_by:
        po.last_opened_at = datetime.utcnow()
        po.last_opened_by = opened_by
        db.commit()
        db.refresh(po)
    return po


@router.put("/{po_id}", response_model=PurchaseOrderOut)
def update_purchase_order(po_id: int, payload: PurchaseOrderUpdate, db: Session = Depends(get_db)):
    po = db.get(PurchaseOrder, po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found.")

    update_data = payload.model_dump(exclude_unset=True, exclude={"line_items"})
    changed_fields = []
    for field, value in update_data.items():
        old_val = getattr(po, field, None)
        if old_val != value:
            changed_fields.append(field)
            setattr(po, field, value)

    # If line_items are provided, replace them
    if payload.line_items is not None:
        changed_fields.append("line_items")
        existing_items = {li.id: li for li in po.line_items if getattr(li, 'id', None)}
        
        new_line_items = []
        for li_data in payload.line_items:
            if li_data.id and li_data.id in existing_items:
                li = existing_items[li_data.id]
                li.item = li_data.item
                li.quantity = li_data.quantity
                li.uom = li_data.uom
                li.unit_price = li_data.unit_price
                li.gst = li_data.gst
                li.freight = li_data.freight
                new_line_items.append(li)
                del existing_items[li_data.id]
            else:
                new_li = POLineItem(
                    item=li_data.item,
                    quantity=li_data.quantity,
                    uom=li_data.uom,
                    unit_price=li_data.unit_price,
                    gst=li_data.gst,
                    freight=li_data.freight
                )
                new_line_items.append(new_li)
                
        po.line_items = new_line_items

        # Update legacy fields from first item
        if po.line_items:
            first = po.line_items[0]
            po.item = first.item
            po.uom = first.uom
            po.unit_price = first.unit_price
            po.total_quantity = sum(li.quantity for li in po.line_items)

    po.last_updated_at = datetime.utcnow()
    
    try:
        db.commit()
        db.refresh(po)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot update or remove line items because they are already linked to Sales/Invoices. Please delete the sales records first."
        )
    
    details_str = f"Updated PO {po.po_number}."
    if changed_fields:
        details_str += f" Changed fields: {', '.join(changed_fields)}"
    
    log_activity(db, "PO Updated", "PurchaseOrder", details_str, po.last_updated_by or "System", po.id)
    return po


@router.delete("/{po_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_purchase_order(po_id: int, db: Session = Depends(get_db)):
    po = db.get(PurchaseOrder, po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found.")
    
    # Check if there are linked sales before deleting
    if po.sales:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete this Purchase Order because it has linked Sales/Invoices. Please delete the sales records first."
        )

    try:
        db.delete(po)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete this Purchase Order due to database constraints (linked records exist)."
        )
