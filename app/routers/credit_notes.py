from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from datetime import datetime

from app.database import get_db
from app.models.models import CreditNote, CreditNoteItem, Sale, WorkOrderSale, SaleItem, WorkOrderSaleItem
from app.schemas.credit_note import CreditNoteCreate, CreditNoteUpdate, CreditNoteOut, AlreadyCreditedItem
from app.utils.helpers import generate_credit_note_number, log_activity
from app.utils.auth import require_admin_for_write
from app.models.models import User

router = APIRouter(prefix="/api/credit-notes", tags=["Credit Notes"])


def _load_cn(cn_id: int, db: Session) -> CreditNote:
    cn = (
        db.query(CreditNote)
        .options(joinedload(CreditNote.items))
        .filter(CreditNote.id == cn_id, CreditNote.is_deleted == False)
        .first()
    )
    if not cn:
        raise HTTPException(status_code=404, detail="Credit Note not found")
    return cn


# ── List ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[CreditNoteOut])
def list_credit_notes(
    sale_type: Optional[str] = None,
    sale_id: Optional[int] = None,
    wo_sale_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    q = (
        db.query(CreditNote)
        .options(joinedload(CreditNote.items))
        .filter(CreditNote.is_deleted == False)
    )
    if sale_type:
        q = q.filter(CreditNote.sale_type == sale_type.upper())
    if sale_id:
        q = q.filter(CreditNote.sale_id == sale_id)
    if wo_sale_id:
        q = q.filter(CreditNote.wo_sale_id == wo_sale_id)
    return q.order_by(CreditNote.created_at.desc()).all()


# ── Already-credited qty per item for a PO sale ──────────────────────────────

@router.get("/already-credited/sale/{sale_id}", response_model=List[AlreadyCreditedItem])
def already_credited_for_sale(sale_id: int, db: Session = Depends(get_db)):
    """Sum of credit_qty already issued for each item on a PO Sale."""
    cns = (
        db.query(CreditNote)
        .options(joinedload(CreditNote.items))
        .filter(
            CreditNote.sale_id == sale_id,
            CreditNote.is_deleted == False,
            CreditNote.status != "Cancelled",
        )
        .all()
    )
    totals: dict[str, float] = {}
    for cn in cns:
        for it in cn.items:
            totals[it.item] = totals.get(it.item, 0.0) + float(it.credit_qty or 0)
    return [AlreadyCreditedItem(item=k, already_credited_qty=v) for k, v in totals.items()]


@router.get("/already-credited/wo-sale/{wo_sale_id}", response_model=List[AlreadyCreditedItem])
def already_credited_for_wo_sale(wo_sale_id: int, db: Session = Depends(get_db)):
    """Sum of credit_qty already issued for each item on a WO Sale."""
    cns = (
        db.query(CreditNote)
        .options(joinedload(CreditNote.items))
        .filter(
            CreditNote.wo_sale_id == wo_sale_id,
            CreditNote.is_deleted == False,
            CreditNote.status != "Cancelled",
        )
        .all()
    )
    totals: dict[str, float] = {}
    for cn in cns:
        for it in cn.items:
            totals[it.item] = totals.get(it.item, 0.0) + float(it.credit_qty or 0)
    return [AlreadyCreditedItem(item=k, already_credited_qty=v) for k, v in totals.items()]


# ── Get single ───────────────────────────────────────────────────────────────

@router.get("/{cn_id}", response_model=CreditNoteOut)
def get_credit_note(cn_id: int, db: Session = Depends(get_db)):
    return _load_cn(cn_id, db)


# ── Create ───────────────────────────────────────────────────────────────────

@router.post("", response_model=CreditNoteOut)
def create_credit_note(payload: CreditNoteCreate, db: Session = Depends(get_db)):
    if payload.sale_type.upper() not in ("PO", "WO"):
        raise HTTPException(status_code=400, detail="sale_type must be PO or WO")
    if payload.sale_type.upper() == "PO" and not payload.sale_id:
        raise HTTPException(status_code=400, detail="sale_id is required for PO credit notes")
    if payload.sale_type.upper() == "WO" and not payload.wo_sale_id:
        raise HTTPException(status_code=400, detail="wo_sale_id is required for WO credit notes")

    cn_number = generate_credit_note_number(db)

    cn = CreditNote(
        cn_number=cn_number,
        cn_date=payload.cn_date,
        sale_type=payload.sale_type.upper(),
        sale_id=payload.sale_id,
        wo_sale_id=payload.wo_sale_id,
        invoice_number=payload.invoice_number,
        po_number=payload.po_number,
        client_name=payload.client_name,
        project=payload.project,
        reason=payload.reason,
        taxable_amount=payload.taxable_amount,
        gst_amount=payload.gst_amount,
        total_amount=payload.total_amount,
        status="Issued",
        created_by=payload.created_by,
    )
    db.add(cn)
    db.flush()  # get cn.id

    for it in payload.items:
        db.add(CreditNoteItem(
            credit_note_id=cn.id,
            item=it.item,
            uom=it.uom,
            original_qty=it.original_qty,
            credit_qty=it.credit_qty,
            unit_price=it.unit_price,
            gst_rate=it.gst_rate,
            subtotal=it.subtotal,
            gst_amount=it.gst_amount,
            total_amount=it.total_amount,
        ))

    db.commit()
    db.refresh(cn)
    log_activity(
        db,
        action="Credit Note Created",
        entity_type="CreditNote",
        entity_id=cn.id,
        entity_name=cn_number,
        details=f"Reason: {cn.reason} | Amount: {cn.total_amount}",
        user=payload.created_by,
    )
    return _load_cn(cn.id, db)


# ── Update (Admin) ────────────────────────────────────────────────────────────

@router.put("/{cn_id}", response_model=CreditNoteOut)
def update_credit_note(
    cn_id: int,
    payload: CreditNoteUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_for_write),
):
    cn = _load_cn(cn_id, db)
    if payload.cn_date is not None:
        cn.cn_date = payload.cn_date
    if payload.reason is not None:
        cn.reason = payload.reason
    if payload.taxable_amount is not None:
        cn.taxable_amount = payload.taxable_amount
    if payload.gst_amount is not None:
        cn.gst_amount = payload.gst_amount
    if payload.total_amount is not None:
        cn.total_amount = payload.total_amount
    if payload.status is not None:
        cn.status = payload.status
    if payload.updated_by is not None:
        cn.updated_by = payload.updated_by

    if payload.items is not None:
        # Replace all items
        db.query(CreditNoteItem).filter(CreditNoteItem.credit_note_id == cn_id).delete()
        for it in payload.items:
            db.add(CreditNoteItem(
                credit_note_id=cn_id,
                item=it.item,
                uom=it.uom,
                original_qty=it.original_qty,
                credit_qty=it.credit_qty,
                unit_price=it.unit_price,
                gst_rate=it.gst_rate,
                subtotal=it.subtotal,
                gst_amount=it.gst_amount,
                total_amount=it.total_amount,
            ))

    db.commit()
    return _load_cn(cn_id, db)


# ── Cancel / Soft-delete (Admin) ─────────────────────────────────────────────

@router.delete("/{cn_id}")
def cancel_credit_note(
    cn_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_for_write),
):
    cn = _load_cn(cn_id, db)
    cn.is_deleted = True
    cn.deleted_at = datetime.utcnow()
    cn.deleted_by = current_user.username if hasattr(current_user, "username") else str(current_user.id)
    cn.status = "Cancelled"
    db.commit()
    return {"detail": f"Credit Note {cn.cn_number} cancelled"}