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
from app.models.models import Sale, PurchaseOrder, WorkOrder, WorkOrderSale, CreditNote, StorePurchaseOrder, StorePONotification, StorePurchaseStockTransaction, StorePurchaseMaterialReceipt, StorePurchaseStockItem, StorePurchaseItem
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
        party_name = obj.client_name
    elif record_type == "purchase_order":
        number = obj.po_number
        party_name = obj.client_name
    elif record_type == "store_purchase_order":
        number = obj.order_number
        party_name = obj.supplier or "Store Supplier"
    elif record_type == "store_material_receipt":
        number = obj.invoice_number or f"Receipt #{obj.id}"
        party_name = obj.vendor_name or "Store Vendor"
    elif record_type == "work_order":
        number = obj.wo_number
        party_name = obj.client_name
    elif record_type == "credit_note":
        number = obj.cn_number
        party_name = obj.client_name
    else:  # work_order_sale
        number = obj.invoice_number or f"WO Sale #{obj.id}"
        party_name = obj.client_name

    return DeletedRecordOut(
        id=obj.id,
        record_type=record_type,
        record_number=number,
        client_name=party_name,
        deleted_by=obj.deleted_by,
        deleted_at=obj.deleted_at,
        permanent_delete_at=obj.permanent_delete_at,
        remaining_hours=_remaining_hours(obj.permanent_delete_at),
    )


# -- List Recently Deleted -----------------------------------------------------

@router.get("", response_model=List[DeletedRecordOut])
def list_recently_deleted(
    module: Optional[str] = Query(None, description="Filter: sale | purchase_order | store_purchase_order | work_order | work_order_sale | credit_note"),
    workspace: Optional[str] = Query(None, description="Workspace filter: Marketing | Store"),
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    require_admin(authorization, db, app_code="any")
    records = []
    now = datetime.utcnow()

    ws = (workspace or "").strip().lower()
    include_marketing = ws != "store"
    include_store = ws != "marketing"

    if include_marketing and module in (None, "all", "sale"):
        sales = (
            db.query(Sale)
            .filter(Sale.is_deleted == True, Sale.permanent_delete_at > now)
            .order_by(Sale.deleted_at.desc())
            .all()
        )
        for s in sales:
            records.append(_build_record("sale", s))

    if include_marketing and module in (None, "all", "purchase_order"):
        pos = (
            db.query(PurchaseOrder)
            .filter(PurchaseOrder.is_deleted == True, PurchaseOrder.permanent_delete_at > now)
            .order_by(PurchaseOrder.deleted_at.desc())
            .all()
        )
        for po in pos:
            records.append(_build_record("purchase_order", po))

    # In Store workspace, the module filter might be sent as 'store_purchase_order' or 'purchase_order'
    store_module_match = module in (None, "all", "store_purchase_order") or (ws == "store" and module in ("purchase_order", "store_po"))
    if include_store and store_module_match:
        store_pos = (
            db.query(StorePurchaseOrder)
            .filter(StorePurchaseOrder.is_deleted == True, StorePurchaseOrder.permanent_delete_at > now)
            .order_by(StorePurchaseOrder.deleted_at.desc())
            .all()
        )
        for spo in store_pos:
            records.append(_build_record("store_purchase_order", spo))

    # Material Receipts (Store workspace only)
    receipt_module_match = module in (None, "all", "store_material_receipt")
    if include_store and receipt_module_match:
        receipts = (
            db.query(StorePurchaseMaterialReceipt)
            .filter(StorePurchaseMaterialReceipt.is_deleted == True, StorePurchaseMaterialReceipt.permanent_delete_at > now)
            .order_by(StorePurchaseMaterialReceipt.deleted_at.desc())
            .all()
        )
        for r in receipts:
            records.append(_build_record("store_material_receipt", r))

    if include_marketing and module in (None, "all", "work_order"):
        wos = (
            db.query(WorkOrder)
            .filter(WorkOrder.is_deleted == True, WorkOrder.permanent_delete_at > now)
            .order_by(WorkOrder.deleted_at.desc())
            .all()
        )
        for wo in wos:
            records.append(_build_record("work_order", wo))

    if include_marketing and module in (None, "all", "work_order_sale"):
        wo_sales = (
            db.query(WorkOrderSale)
            .filter(WorkOrderSale.is_deleted == True, WorkOrderSale.permanent_delete_at > now)
            .order_by(WorkOrderSale.deleted_at.desc())
            .all()
        )
        for wos in wo_sales:
            records.append(_build_record("work_order_sale", wos))

    if include_marketing and module in (None, "all", "credit_note"):
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
    user = require_admin(authorization, db, app_code="any")
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

    elif record_type == "store_purchase_order":
        obj = db.query(StorePurchaseOrder).filter(StorePurchaseOrder.id == record_id, StorePurchaseOrder.is_deleted == True).first()
        if not obj:
            raise HTTPException(status_code=404, detail="Deleted Store Purchase Order not found.")
        if obj.permanent_delete_at and obj.permanent_delete_at < now:
            raise HTTPException(status_code=410, detail="Store Purchase Order has already expired.")
        obj.is_deleted = False
        obj.deleted_at = None
        obj.deleted_by = None
        obj.permanent_delete_at = None
        db.commit()
        db.refresh(obj)
        log_activity(db, "Store PO Restored", "StorePurchaseOrder",
            f"Restored Store PO {obj.order_number} from Recently Deleted.",
            user.name, obj.id, entity_name=obj.order_number, workspace="Store")
        return _build_record("store_purchase_order", obj)

    elif record_type == "store_material_receipt":
        from datetime import timedelta
        from app.utils.helpers import log_activity as _log
        obj = db.query(StorePurchaseMaterialReceipt).filter(
            StorePurchaseMaterialReceipt.id == record_id,
            StorePurchaseMaterialReceipt.is_deleted == True
        ).first()
        if not obj:
            raise HTTPException(status_code=404, detail="Deleted Material Receipt not found.")
        if obj.permanent_delete_at and obj.permanent_delete_at < now:
            raise HTTPException(status_code=410, detail="Material Receipt has already expired.")
        # Restore: re-post inventory transactions
        from app.routers.store_purchase import _record_receipt, _inventory_group_and_item, _refresh_po_received_status
        obj.is_deleted = False
        obj.deleted_at = None
        obj.deleted_by = None
        obj.permanent_delete_at = None
        db.flush()
        # Re-add inventory postings for each receipt line
        for line in obj.lines:
            if (line.received_quantity or 0) > 0:
                group_name, item_name = _inventory_group_and_item(db, line.item_description)
                _record_receipt(
                    db, name=group_name, stock_item=item_name,
                    quantity=line.received_quantity, rate=line.unit_rate,
                    vendor_name=obj.vendor_name, reference_no=obj.order.order_number,
                    receipt_date=obj.invoice_date, uom=line.uom,
                    user_name=user.name, receipt_line_id=line.id,
                )
        _refresh_po_received_status(db, obj.order)
        db.commit()
        db.refresh(obj)
        log_activity(db, "Material Receipt Restored", "StorePurchaseOrder",
            f"Restored Material Receipt (Invoice: {obj.invoice_number}) from Recently Deleted.",
            user.name, obj.order_id, entity_name=obj.invoice_number, workspace="Store")
        return _build_record("store_material_receipt", obj)

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
    user = require_admin(authorization, db, app_code="any")

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

    elif record_type == "store_purchase_order":
        obj = db.query(StorePurchaseOrder).filter(StorePurchaseOrder.id == record_id, StorePurchaseOrder.is_deleted == True).first()
        if not obj:
            raise HTTPException(status_code=404, detail="Deleted Store Purchase Order not found.")
        label = obj.order_number
        db.query(StorePONotification).filter(StorePONotification.order_id == record_id).delete(synchronize_session=False)
        for rcpt in (obj.material_receipts or []):
            line_ids = [line.id for line in (rcpt.lines or [])]
            if line_ids:
                db.query(StorePurchaseStockTransaction).filter(StorePurchaseStockTransaction.receipt_line_id.in_(line_ids)).update({StorePurchaseStockTransaction.receipt_line_id: None}, synchronize_session=False)
        db.delete(obj)
        db.commit()
        log_activity(db, "Store PO Permanently Deleted", "StorePurchaseOrder",
            f"Admin permanently deleted Store PO {label}.", user.name, record_id, entity_name=label, workspace="Store")

    elif record_type == "store_material_receipt":
        obj = db.query(StorePurchaseMaterialReceipt).filter(
            StorePurchaseMaterialReceipt.id == record_id,
            StorePurchaseMaterialReceipt.is_deleted == True
        ).first()
        if not obj:
            raise HTTPException(status_code=404, detail="Deleted Material Receipt not found.")
        label = obj.invoice_number or f"Receipt #{obj.id}"
        line_ids = [line.id for line in (obj.lines or [])]
        if line_ids:
            db.query(StorePurchaseStockTransaction).filter(
                StorePurchaseStockTransaction.receipt_line_id.in_(line_ids)
            ).update({StorePurchaseStockTransaction.receipt_line_id: None}, synchronize_session=False)
        db.delete(obj)
        db.commit()
        log_activity(db, "Material Receipt Permanently Deleted", "StorePurchaseOrder",
            f"Admin permanently deleted Material Receipt {label}.", user.name, record_id, entity_name=label, workspace="Store")

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

