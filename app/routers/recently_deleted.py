"""
Recently Deleted / Recycle Bin Router
Admin-only: list, restore, permanently delete soft-deleted Sales, POs, WOs, WO Sales and Credit Notes.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Header, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel
import os

from app.database import get_db
from app.models.models import Sale, PurchaseOrder, WorkOrder, WorkOrderSale, CreditNote
from app.utils.auth import require_admin
from app.utils.helpers import log_activity, recalc_po_delivered_quantities, recalc_wo_completed_quantities

router = APIRouter(prefix="/api/recently-deleted", tags=["Recently Deleted"])


# -- Response Schema -----------------------------------------------------------

class DeletedRecordOut(BaseModel):
    id: int
    record_type: str
    record_number: str
    client_name: str
    deleted_by: Optional[str]
    deleted_at: Optional[datetime]
    permanent_delete_at: Optional[datetime]
    remaining_hours: Optional[float]

    model_config = {"from_attributes": True}


def _remaining_hours(permanent_delete_at):
    if permanent_delete_at is None:
        return None
    now = datetime.utcnow()
    diff = (permanent_delete_at - now).total_seconds()
    return max(round(diff / 3600, 2), 0.0)


def _build_record(record_type, obj):
    if record_type == "sale":
        number = obj.invoice_number or f"Sale #{obj.id}"
    elif record_type == "purchase_order":
        number = obj.po_number
    elif record_type == "work_order":
        number = obj.wo_number
    elif record_type == "credit_note":
        number = obj.cn_number
    else:  # work_order_sale
        number = obj.invoice_number or f"WO Sale #{obj.id}"

    return DeletedRecordOut(
        id=obj.id,
        record_type=record_type,
        record_number=number,
        client_name=obj.client_name,
        deleted_by=obj.deleted_by,
        deleted_at=obj.deleted_at,
        permanent_delete_at=obj.permanent_delete_at,
        remaining_hours=_remaining_hours(obj.permanent_delete_at),
    )


# -- List Recently Deleted -----------------------------------------------------

@router.get("", response_model=List[DeletedRecordOut])
def list_recently_deleted(
    module: Optional[str] = Query(None, description="Filter: sale | purchase_order | work_order | work_order_sale | credit_note"),
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db)
    records = []
    now = datetime.utcnow()

    if module in (None, "all", "sale"):
        sales = (
            db.query(Sale)
            .filter(Sale.is_deleted == True, Sale.permanent_delete_at > now)
            .order_by(Sale.deleted_at.desc())
            .all()
        )
        for s in sales:
            records.append(_build_record("sale", s))

    if module in (None, "all", "purchase_order"):
        pos = (
            db.query(PurchaseOrder)
            .filter(PurchaseOrder.is_deleted == True, PurchaseOrder.permanent_delete_at > now)
            .order_by(PurchaseOrder.deleted_at.desc())
            .all()
        )
        for po in pos:
            records.append(_build_record("purchase_order", po))

    if module in (None, "all", "work_order"):
        wos = (
            db.query(WorkOrder)
            .filter(WorkOrder.is_deleted == True, WorkOrder.permanent_delete_at > now)
            .order_by(WorkOrder.deleted_at.desc())
            .all()
        )
        for wo in wos:
            records.append(_build_record("work_order", wo))

    if module in (None, "all", "work_order_sale"):
        wo_sales = (
            db.query(WorkOrderSale)
            .filter(WorkOrderSale.is_deleted == True, WorkOrderSale.permanent_delete_at > now)
            .order_by(WorkOrderSale.deleted_at.desc())
            .all()
        )
        for wos in wo_sales:
            records.append(_build_record("work_order_sale", wos))

    if module in (None, "all", "credit_note"):
        credit_notes = (
            db.query(CreditNote)
            .filter(CreditNote.is_deleted == True, CreditNote.permanent_delete_at > now)
            .order_by(CreditNote.deleted_at.desc())
            .all()
        )
        for cn in credit_notes:
            records.append(_build_record("credit_note", cn))

    records.sort(key=lambda r: r.deleted_at or datetime.min, reverse=True)
    return records


# -- Restore -------------------------------------------------------------------

@router.post("/{record_type}/{record_id}/restore", response_model=DeletedRecordOut)
def restore_record(
    record_type: str,
    record_id: int,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    user = require_admin(authorization, db)
    now = datetime.utcnow()

    if record_type == "sale":
        obj = db.query(Sale).filter(Sale.id == record_id, Sale.is_deleted == True).first()
        if not obj:
            raise HTTPException(status_code=404, detail="Deleted sale not found.")
        if obj.permanent_delete_at and obj.permanent_delete_at < now:
            raise HTTPException(status_code=410, detail="Sale has already expired.")
        obj.is_deleted = False
        obj.deleted_at = None
        obj.deleted_by = None
        obj.permanent_delete_at = None
        db.flush()
        # Recalculate PO delivered quantities so pending amounts are correct
        if obj.po_id:
            po = db.query(PurchaseOrder).filter(PurchaseOrder.id == obj.po_id).first()
            if po:
                recalc_po_delivered_quantities(db, po)
        db.commit()
        db.refresh(obj)
        log_activity(db, "Sale Restored", "Sale",
            f"Restored sale invoice {obj.invoice_number} from Recently Deleted.",
            user.name, obj.id, entity_name=obj.invoice_number)
        return _build_record("sale", obj)

    elif record_type == "purchase_order":
        obj = db.query(PurchaseOrder).filter(PurchaseOrder.id == record_id, PurchaseOrder.is_deleted == True).first()
        if not obj:
            raise HTTPException(status_code=404, detail="Deleted purchase order not found.")
        if obj.permanent_delete_at and obj.permanent_delete_at < now:
            raise HTTPException(status_code=410, detail="Purchase Order has already expired.")
        obj.is_deleted = False
        obj.deleted_at = None
        obj.deleted_by = None
        obj.permanent_delete_at = None
        db.commit()
        db.refresh(obj)
        log_activity(db, "PO Restored", "PurchaseOrder",
            f"Restored PO {obj.po_number} from Recently Deleted.",
            user.name, obj.id, entity_name=obj.po_number)
        return _build_record("purchase_order", obj)

    elif record_type == "work_order":
        obj = db.query(WorkOrder).filter(WorkOrder.id == record_id, WorkOrder.is_deleted == True).first()
        if not obj:
            raise HTTPException(status_code=404, detail="Deleted work order not found.")
        if obj.permanent_delete_at and obj.permanent_delete_at < now:
            raise HTTPException(status_code=410, detail="Work Order has already expired.")
        obj.is_deleted = False
        obj.deleted_at = None
        obj.deleted_by = None
        obj.permanent_delete_at = None
        db.commit()
        db.refresh(obj)
        log_activity(db, "WO Restored", "WorkOrder",
            f"Restored WO {obj.wo_number} from Recently Deleted.",
            user.name, obj.id, entity_name=obj.wo_number)
        return _build_record("work_order", obj)

    elif record_type == "work_order_sale":
        obj = db.query(WorkOrderSale).filter(WorkOrderSale.id == record_id, WorkOrderSale.is_deleted == True).first()
        if not obj:
            raise HTTPException(status_code=404, detail="Deleted WO Sale not found.")
        if obj.permanent_delete_at and obj.permanent_delete_at < now:
            raise HTTPException(status_code=410, detail="WO Sale has already expired.")
        obj.is_deleted = False
        obj.deleted_at = None
        obj.deleted_by = None
        obj.permanent_delete_at = None
        db.flush()
        # Recalculate WO completed quantities so pending amounts are correct
        if obj.wo_id:
            wo = db.query(WorkOrder).filter(WorkOrder.id == obj.wo_id).first()
            if wo:
                recalc_wo_completed_quantities(db, wo)
        db.commit()
        db.refresh(obj)
        log_activity(db, "WO Sale Restored", "WorkOrderSale",
            f"Restored WO Sale invoice {obj.invoice_number} from Recently Deleted.",
            user.name, obj.id, entity_name=obj.invoice_number)
        return _build_record("work_order_sale", obj)

    elif record_type == "credit_note":
        obj = db.query(CreditNote).filter(CreditNote.id == record_id, CreditNote.is_deleted == True).first()
        if not obj:
            raise HTTPException(status_code=404, detail="Deleted Credit Note not found.")
        if obj.permanent_delete_at and obj.permanent_delete_at < now:
            raise HTTPException(status_code=410, detail="Credit Note has already expired.")
        obj.is_deleted = False
        obj.deleted_at = None
        obj.deleted_by = None
        obj.permanent_delete_at = None
        obj.status = "Issued"
        db.commit()
        db.refresh(obj)
        log_activity(db, "Credit Note Restored", "CreditNote",
            f"Restored Credit Note {obj.cn_number} from Recently Deleted.",
            user.name, obj.id, entity_name=obj.cn_number)
        return _build_record("credit_note", obj)

    else:
        raise HTTPException(status_code=400, detail=f"Invalid record_type '{record_type}'.")


# -- Permanent Delete ----------------------------------------------------------

@router.delete("/{record_type}/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def permanent_delete_record(
    record_type: str,
    record_id: int,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    user = require_admin(authorization, db)

    def _delete_file(url_str):
        if not url_str:
            return
        for url in url_str.split(";"):
            if url and url.strip():
                fp = url.strip().lstrip("/")
                if os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except Exception:
                        pass

    if record_type == "sale":
        obj = db.query(Sale).filter(Sale.id == record_id, Sale.is_deleted == True).first()
        if not obj:
            raise HTTPException(status_code=404, detail="Deleted sale not found.")
        label = obj.invoice_number or f"Sale #{obj.id}"
        _delete_file(obj.invoice_url)
        _delete_file(obj.e_way_bill_url)
        _delete_file(obj.delivery_challan_url)
        db.delete(obj)
        db.commit()
        log_activity(db, "Sale Permanently Deleted", "Sale",
            f"Admin permanently deleted sale {label}.", user.name, record_id, entity_name=label)

    elif record_type == "purchase_order":
        obj = db.query(PurchaseOrder).filter(PurchaseOrder.id == record_id, PurchaseOrder.is_deleted == True).first()
        if not obj:
            raise HTTPException(status_code=404, detail="Deleted purchase order not found.")
        label = obj.po_number
        _delete_file(obj.file_url)
        db.delete(obj)
        db.commit()
        log_activity(db, "PO Permanently Deleted", "PurchaseOrder",
            f"Admin permanently deleted PO {label}.", user.name, record_id, entity_name=label)

    elif record_type == "work_order":
        obj = db.query(WorkOrder).filter(WorkOrder.id == record_id, WorkOrder.is_deleted == True).first()
        if not obj:
            raise HTTPException(status_code=404, detail="Deleted work order not found.")
        label = obj.wo_number
        _delete_file(obj.file_url)
        db.delete(obj)
        db.commit()
        log_activity(db, "WO Permanently Deleted", "WorkOrder",
            f"Admin permanently deleted WO {label}.", user.name, record_id, entity_name=label)

    elif record_type == "work_order_sale":
        obj = db.query(WorkOrderSale).filter(WorkOrderSale.id == record_id, WorkOrderSale.is_deleted == True).first()
        if not obj:
            raise HTTPException(status_code=404, detail="Deleted WO Sale not found.")
        label = obj.invoice_number or f"WO Sale #{obj.id}"
        _delete_file(obj.invoice_url)
        _delete_file(obj.e_way_bill_url)
        _delete_file(obj.delivery_challan_url)
        db.delete(obj)
        db.commit()
        log_activity(db, "WO Sale Permanently Deleted", "WorkOrderSale",
            f"Admin permanently deleted WO Sale {label}.", user.name, record_id, entity_name=label)

    elif record_type == "credit_note":
        obj = db.query(CreditNote).filter(CreditNote.id == record_id, CreditNote.is_deleted == True).first()
        if not obj:
            raise HTTPException(status_code=404, detail="Deleted Credit Note not found.")
        label = obj.cn_number
        db.delete(obj)
        db.commit()
        log_activity(db, "Credit Note Permanently Deleted", "CreditNote",
            f"Admin permanently deleted Credit Note {label}.", user.name, record_id, entity_name=label)

    else:
        raise HTTPException(status_code=400, detail=f"Invalid record_type '{record_type}'.")

