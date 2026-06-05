from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime
import os
import uuid
from pydantic import BaseModel
from app.database import get_db
from app.utils.helpers import generate_invoice_number, compute_sale_financials, log_activity
from app.models.models import Sale, SaleActivity, PurchaseOrder, SaleItem, POLineItem
from app.schemas.sale import SaleCreate, SaleUpdate, SaleOut, SaleActivityCreate, SaleActivityOut, SaleItemCreate
from app.services.storage import save_upload

router = APIRouter(prefix="/api/sales", tags=["Sales"])


class MarkDeliveredPayload(BaseModel):
    delivery_challan_url: str
    updated_by: Optional[str] = None


@router.get("", response_model=List[SaleOut])
def list_sales(
    po_id: Optional[int] = None,
    client: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    q = db.query(Sale)
    if po_id:
        q = q.filter(Sale.po_id == po_id)
    if client:
        q = q.filter(Sale.client_name.ilike(f"%{client}%"))
    return q.order_by(Sale.created_at.desc()).offset(skip).limit(limit).all()


@router.post("/upload")
async def upload_invoice_file(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename)[1]
    filename = f"{uuid.uuid4()}{ext}"
    file_bytes = await file.read()
    file_url = save_upload(file_bytes, filename, file.content_type or "application/octet-stream")
    return {"file_url": file_url}


@router.post("", response_model=SaleOut, status_code=status.HTTP_201_CREATED)
def create_sale(payload: SaleCreate, db: Session = Depends(get_db)):
    po = db.get(PurchaseOrder, payload.po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found.")

    invoice_number = payload.invoice_number or generate_invoice_number(db)

    sale = Sale(
        po_id=payload.po_id,
        po_number=payload.po_number,
        invoice_number=invoice_number,
        client_name=payload.client_name,
        project=payload.project,
        subtotal=payload.subtotal,
        gst_amount=payload.gst_amount,
        freight=payload.freight,
        grand_total=payload.grand_total,
        payment_status=payload.payment_status,
        payment_note=payload.payment_note,
        invoice_url=payload.invoice_url,
        e_way_bill_url=payload.e_way_bill_url,
        dispatch_from=payload.dispatch_from,
        ship_to=payload.ship_to,
        bill_to=payload.bill_to,
        dispatched_through=payload.dispatched_through,
        e_way_bill_no=payload.e_way_bill_no,
        buyers_order_no=payload.buyers_order_no,
        payment_terms=payload.payment_terms,
        hsn_code=payload.hsn_code,
        created_by=payload.created_by,
    )
    db.add(sale)
    db.flush()

    for item_data in payload.items:
        sale_item = SaleItem(
            sale_id=sale.id,
            line_item_id=item_data.line_item_id,
            item=item_data.item,
            uom=item_data.uom,
            quantity=item_data.quantity,
            unit_price=item_data.unit_price,
            gst_rate=item_data.gst_rate,
            subtotal=item_data.subtotal,
            gst_amount=item_data.gst_amount,
            total_amount=item_data.total_amount,
        )
        db.add(sale_item)

        # Update delivery tracking
        po.delivered_quantity += item_data.quantity
        if item_data.line_item_id:
            li = db.get(POLineItem, item_data.line_item_id)
            if li:
                li.delivered_quantity += item_data.quantity

    activity = SaleActivity(
        sale_id=sale.id,
        action="Sale Created",
        note=payload.payment_note,
        payment_status=payload.payment_status,
        by=payload.created_by,
    )
    db.add(activity)
    db.commit()
    db.refresh(sale)
    
    log_activity(db, "Sale Created", "Sale", f"Created sale invoice {sale.invoice_number} for {po.client_name}.", payload.created_by, sale.id)
    return sale


@router.get("/{sale_id}", response_model=SaleOut)
def get_sale(sale_id: int, db: Session = Depends(get_db)):
    sale = db.get(Sale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found.")
    return sale


@router.put("/{sale_id}", response_model=SaleOut)
def update_sale(sale_id: int, payload: SaleUpdate, db: Session = Depends(get_db)):
    sale = db.get(Sale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found.")

    updates = payload.model_dump(exclude_unset=True)
    updated_by = updates.pop("updated_by", None)

    if "items" in updates:
        new_items_data = updates.pop("items")
        po = db.get(PurchaseOrder, sale.po_id)
        
        # 1. Rollback old quantities
        for old_item in sale.items:
            if po:
                po.delivered_quantity = max(0, po.delivered_quantity - old_item.quantity)
            if old_item.line_item_id:
                li = db.get(POLineItem, old_item.line_item_id)
                if li:
                    li.delivered_quantity = max(0, li.delivered_quantity - old_item.quantity)
        
        # 2. Delete old items
        db.query(SaleItem).filter(SaleItem.sale_id == sale.id).delete()
        
        # 3. Add new items and update PO
        for item_data in new_items_data:
            # item_data is a dict because of model_dump()
            li_id = item_data.get("line_item_id") if (item_data.get("line_item_id") and item_data.get("line_item_id") > 0) else None
            
            new_item = SaleItem(
                sale_id=sale.id,
                line_item_id=li_id,
                item=item_data.get("item"),
                uom=item_data.get("uom", "Nos"),
                quantity=item_data.get("quantity", 0),
                unit_price=item_data.get("unit_price", 0),
                gst_rate=item_data.get("gst_rate", 0),
                subtotal=item_data.get("subtotal", 0),
                gst_amount=item_data.get("gst_amount", 0),
                total_amount=item_data.get("total_amount", 0)
            )
            db.add(new_item)
            if po:
                po.delivered_quantity += item_data.get("quantity", 0)
            if li_id:
                li = db.get(POLineItem, li_id)
                if li:
                    li.delivered_quantity += item_data.get("quantity", 0)

    changed_fields = []
    for field, value in updates.items():
        old_val = getattr(sale, field, None)
        if old_val != value:
            changed_fields.append(field)
            setattr(sale, field, value)

    sale.updated_by = updated_by
    sale.updated_at = datetime.utcnow()

    if "payment_status" in changed_fields or "items" in payload.model_dump(exclude_unset=True):
        action = "Sale Details Updated"
        if "payment_status" in changed_fields:
            action = "Payment Status Updated"
            
        activity = SaleActivity(
            sale_id=sale.id,
            action=action,
            note=payload.payment_note,
            payment_status=str(updates.get("payment_status", sale.payment_status.value if hasattr(sale.payment_status, 'value') else sale.payment_status)),
            by=updated_by,
        )
        db.add(activity)

    db.commit()
    db.refresh(sale)
    
    details_str = f"Updated sale invoice {sale.invoice_number}."
    if changed_fields:
        details_str += f" Changed fields: {', '.join(changed_fields)}"
    
    log_activity(db, "Sale Updated", "Sale", details_str, updated_by or "System", sale.id)
    return sale


@router.put("/{sale_id}/mark-delivered", response_model=SaleOut)
def mark_delivered(
    sale_id: int,
    payload: MarkDeliveredPayload,
    db: Session = Depends(get_db),
):
    """Mark a sale as Delivered. Requires delivery_challan_url to be supplied."""
    sale = db.get(Sale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found.")

    challan_url = payload.delivery_challan_url or sale.delivery_challan_url
    if not challan_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A delivery challan document must be uploaded before marking as Delivered.",
        )

    sale.delivery_status = "Delivered"
    sale.delivery_challan_url = challan_url
    sale.updated_by = payload.updated_by
    sale.updated_at = datetime.utcnow()

    activity = SaleActivity(
        sale_id=sale.id,
        action="Marked Delivered",
        note="Sale marked as delivered with challan document.",
        payment_status=sale.payment_status.value if hasattr(sale.payment_status, 'value') else str(sale.payment_status),
        by=payload.updated_by,
    )
    db.add(activity)
    db.commit()
    db.refresh(sale)
    log_activity(db, "Sale Marked Delivered", "Sale", f"Sale invoice {sale.invoice_number} marked as Delivered.", payload.updated_by, sale.id)
    return sale


@router.delete("/{sale_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sale(sale_id: int, db: Session = Depends(get_db)):
    sale = db.get(Sale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found.")

    po = db.get(PurchaseOrder, sale.po_id)
    
    # Rollback quantities for all items in this sale
    for item in sale.items:
        if po:
            po.delivered_quantity = max(0, po.delivered_quantity - item.quantity)
        if item.line_item_id:
            li = db.get(POLineItem, item.line_item_id)
            if li:
                li.delivered_quantity = max(0, li.delivered_quantity - item.quantity)

    db.delete(sale)
    db.commit()
    log_activity(db, "Sale Deleted", "Sale", f"Deleted sale invoice for {sale.client_name}.", "System/Admin", sale_id)


@router.post("/{sale_id}/activities", response_model=SaleActivityOut, status_code=status.HTTP_201_CREATED)
def add_activity(sale_id: int, payload: SaleActivityCreate, db: Session = Depends(get_db)):
    sale = db.get(Sale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found.")
    activity = SaleActivity(
        sale_id=sale_id,
        action=payload.action,
        note=payload.note,
        payment_status=payload.payment_status,
        by=payload.by,
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity
