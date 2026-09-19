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
            # Stored quantities are signed financial adjustments; the UI's
            # "already credited" / remaining-quantity aid needs magnitude.
            totals[it.item] = totals.get(it.item, 0.0) + abs(float(it.credit_qty or 0))
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
            totals[it.item] = totals.get(it.item, 0.0) + abs(float(it.credit_qty or 0))
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
    # sale_id/wo_sale_id are deliberately optional: historical invoices may
    # not exist in the app and are entered manually.
    if not (payload.client_name or "").strip():
        raise HTTPException(status_code=400, detail="client_name is required")

    # A linked invoice is the authoritative source for its header values. A
    # manual note has no linked id, so only then do we retain user-entered
    # invoice details (which makes pre-April-2026 invoices fully supported).
    source_sale = None
    if payload.sale_type.upper() == "PO" and payload.sale_id:
        source_sale = db.query(Sale).filter(Sale.id == payload.sale_id, Sale.is_deleted == False).first()
    elif payload.sale_type.upper() == "WO" and payload.wo_sale_id:
        source_sale = db.query(WorkOrderSale).filter(WorkOrderSale.id == payload.wo_sale_id, WorkOrderSale.is_deleted == False).first()
    if (payload.sale_id or payload.wo_sale_id) and not source_sale:
        raise HTTPException(status_code=404, detail="Selected invoice was not found")

    invoice_number = source_sale.invoice_number if source_sale else payload.invoice_number
    po_number = (getattr(source_sale, "po_number", None) or getattr(source_sale, "wo_number", None)) if source_sale else payload.po_number
    client_name = source_sale.client_name if source_sale else payload.client_name
    project = getattr(source_sale, "project", None) if source_sale else payload.project

    cn_number = generate_credit_note_number(db)

    cn = CreditNote(
        cn_number=cn_number,
        cn_date=payload.cn_date,
        sale_type=payload.sale_type.upper(),
        sale_id=payload.sale_id,
        wo_sale_id=payload.wo_sale_id,
        invoice_number=invoice_number,
        po_number=po_number,
        client_name=client_name,
        project=project,
        reason=payload.reason,
        taxable_amount=0,
        gst_amount=0,
        total_amount=0,
        status="Issued",
        created_by=payload.created_by,
    )
    db.add(cn)
    db.flush()  # get cn.id

    taxable_amount = gst_amount = total_amount = 0.0
    for it in payload.items:
        # Quantity Less is always a reduction and Quantity Excess is always
        # an increase. For all other corrections a typed +/- quantity is
        # retained, allowing explicit amount increase/decrease adjustments.
        quantity = float(it.credit_qty or 0)
        if payload.reason == "Quantity Less":
            quantity = -abs(quantity)
        elif payload.reason == "Quantity Excess":
            quantity = abs(quantity)
        subtotal = quantity * float(it.unit_price or 0)
        item_gst = subtotal * float(it.gst_rate or 0) / 100
        item_total = subtotal + item_gst
        taxable_amount += subtotal
        gst_amount += item_gst
        total_amount += item_total
        db.add(CreditNoteItem(
            credit_note_id=cn.id,
            item=it.item,
            uom=it.uom,
            original_qty=it.original_qty,
            credit_qty=quantity,
            unit_price=it.unit_price,
            gst_rate=it.gst_rate,
            subtotal=subtotal,
            gst_amount=item_gst,
            total_amount=item_total,
        ))
    cn.taxable_amount = taxable_amount
    cn.gst_amount = gst_amount
    cn.total_amount = total_amount

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
    for field in ("sale_id", "wo_sale_id", "invoice_number", "po_number", "client_name", "project"):
        value = getattr(payload, field)
        if value is not None:
            setattr(cn, field, value)

    if payload.items is not None:
        # Replace all items
        db.query(CreditNoteItem).filter(CreditNoteItem.credit_note_id == cn_id).delete()
        taxable_amount = gst_amount = total_amount = 0.0
        for it in payload.items:
            quantity = float(it.credit_qty or 0)
            if cn.reason == "Quantity Less":
                quantity = -abs(quantity)
            elif cn.reason == "Quantity Excess":
                quantity = abs(quantity)
            subtotal = quantity * float(it.unit_price or 0)
            item_gst = subtotal * float(it.gst_rate or 0) / 100
            item_total = subtotal + item_gst
            taxable_amount += subtotal
            gst_amount += item_gst
            total_amount += item_total
            db.add(CreditNoteItem(
                credit_note_id=cn_id,
                item=it.item,
                uom=it.uom,
                original_qty=it.original_qty,
                credit_qty=quantity,
                unit_price=it.unit_price,
                gst_rate=it.gst_rate,
                subtotal=subtotal,
                gst_amount=item_gst,
                total_amount=item_total,
            ))
        cn.taxable_amount = taxable_amount
        cn.gst_amount = gst_amount
        cn.total_amount = total_amount

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
