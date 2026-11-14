from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.utils.auth import get_current_user, application_role
from app.models.models import (
    User, StorePurchaseOrder, StorePurchaseOrderDetail,
    StorePurchaseOrderLine, StorePurchaseMaterialReceipt,
    StorePurchaseMaterialReceiptLine,
    PurchaseOrder, WorkOrder,
    Sale, SaleItem, WorkOrderSale, WorkOrderSaleItem,
    CreditNote, CreditNoteItem
)
from app.utils.helpers import compute_sale_taxable_and_gst, compute_sale_grand_total

router = APIRouter(prefix="/api/accounts", tags=["Accounts"])


def accounts_user(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    is_super = bool(user.is_super_admin or (user.email and user.email.lower() == "deepikar412003@gmail.com"))
    if not is_super:
        role = application_role(user, db, "accounts")
        if role == "none":
            raise HTTPException(status_code=403, detail="No access to Accounts workspace.")
    return user


# ── Store Accounts: 1 row per Materials Received entry (matches Store Materials Received) ──
@router.get("/store")
def get_store_accounts(user: User = Depends(accounts_user), db: Session = Depends(get_db)):
    """
    Returns 1 entry per Materials Received transaction (matching the 9 entries in Store -> Materials Received).
    Each receipt entry aggregates its items, received quantity, and total amount.
    """
    receipts = (
        db.query(StorePurchaseMaterialReceipt)
        .filter(StorePurchaseMaterialReceipt.is_deleted == False)
        .options(
            joinedload(StorePurchaseMaterialReceipt.lines),
            joinedload(StorePurchaseMaterialReceipt.order).joinedload(StorePurchaseOrder.details),
            joinedload(StorePurchaseMaterialReceipt.order).joinedload(StorePurchaseOrder.line_items),
        )
        .order_by(StorePurchaseMaterialReceipt.invoice_date.desc(), StorePurchaseMaterialReceipt.id.desc())
        .all()
    )

    rows = []
    sno = 1

    for receipt in receipts:
        po = receipt.order
        details = po.details if po else None
        po_date = details.po_date.strftime("%Y-%m-%d") if details and details.po_date else None
        po_number = po.order_number if po else "—"
        vendor_name = receipt.vendor_name or (po.supplier if po else "—")

        lines = receipt.lines or []
        items_count = len(lines)
        first_item = lines[0].item_description if items_count > 0 else (po.item_name if po else "—")

        # Aggregate quantities and amount for this receiving entry
        total_ord_qty = sum(float(l.ordered_quantity or 0) for l in lines)
        total_recd_qty = sum(float(l.received_quantity or 0) for l in lines)
        total_amt = sum(float(l.amount or 0) for l in lines) + float(receipt.freight_charge or 0)

        # Check overall PO completion status
        is_fully_received = True
        if po and po.line_items:
            # Check total received for each PO line item across all receipts of this PO
            total_recd_by_item = {}
            for r in (po.material_receipts or []):
                if not getattr(r, "is_deleted", False):
                    for rl in (r.lines or []):
                        k = (rl.item_description or "").strip().lower()
                        total_recd_by_item[k] = total_recd_by_item.get(k, 0.0) + float(rl.received_quantity or 0)

            for pl in po.line_items:
                k = (pl.item_description or "").strip().lower()
                ord_q = float(pl.quantity or 0)
                recd_q = total_recd_by_item.get(k, 0.0)
                if recd_q < ord_q:
                    is_fully_received = False
                    break
        else:
            is_fully_received = total_recd_qty >= total_ord_qty if total_ord_qty > 0 else True

        status = "Fully Received" if is_fully_received else "Partially Received"

        # Item line breakdown for expandable view / modal
        item_breakdown = [
            {
                "item_description": l.item_description,
                "ordered_quantity": float(l.ordered_quantity or 0),
                "received_quantity": float(l.received_quantity or 0),
                "uom": getattr(l, "uom", "Nos") or "Nos",
                "unit_rate": float(l.unit_rate or 0),
                "amount": float(l.amount or 0),
            }
            for l in lines
        ]

        rows.append({
            "sno": sno,
            "po_id": po.id if po else None,
            "receipt_id": receipt.id,
            "receipt_number": receipt.invoice_number or f"MR-{receipt.id}",
            "vendor_name": vendor_name,
            "item_name": first_item,
            "items_count": items_count,
            "items": item_breakdown,
            "po_number": po_number,
            "po_date": po_date,
            "order_qty": round(total_ord_qty, 3),
            "received_qty": round(total_recd_qty, 3),
            "amount": round(total_amt, 2),
            "status": status,
            "received_date": receipt.invoice_date.strftime("%Y-%m-%d") if receipt.invoice_date else None,
            "received_by": receipt.created_by or "",
            "payment_status": receipt.payment_status or "Unpaid",
            "freight_charge": float(receipt.freight_charge or 0),
        })
        sno += 1

    return rows


# ── Store Accounts: detail for one PO ────────────────────────────────────────
@router.get("/store/{po_id}")
def get_store_account_detail(po_id: int, user: User = Depends(accounts_user), db: Session = Depends(get_db)):
    po = (
        db.query(StorePurchaseOrder)
        .filter(StorePurchaseOrder.id == po_id, StorePurchaseOrder.is_deleted == False)
        .options(
            joinedload(StorePurchaseOrder.details),
            joinedload(StorePurchaseOrder.line_items),
            joinedload(StorePurchaseOrder.material_receipts)
            .joinedload(StorePurchaseMaterialReceipt.lines),
        )
        .first()
    )
    if not po:
        raise HTTPException(status_code=404, detail="PO not found.")

    details = po.details
    po_date = details.po_date.strftime("%Y-%m-%d") if details and details.po_date else None

    lines_out = []
    for line in (po.line_items or []):
        lines_out.append({
            "id": line.id,
            "item_description": line.item_description,
            "uom": getattr(line, "uom", None) or "Nos",
            "quantity": float(line.quantity or 0),
            "unit_rate": float(line.unit_rate or 0),
            "gst_rate": float(getattr(details, "gst_rate", 18) or 18) if details else 18,
            "amount": float(line.amount or 0),
        })

    receipts_out = []
    for receipt in (po.material_receipts or []):
        if getattr(receipt, "is_deleted", False):
            continue
        receipt_lines = []
        for rl in (receipt.lines or []):
            receipt_lines.append({
                "id": rl.id,
                "item_description": rl.item_description,
                "ordered_quantity": float(rl.ordered_quantity or 0),
                "received_quantity": float(rl.received_quantity or 0),
                "uom": getattr(rl, "uom", "Nos"),
                "unit_rate": float(rl.unit_rate or 0),
                "amount": float(rl.amount or 0),
            })
        receipts_out.append({
            "id": receipt.id,
            "receipt_number": receipt.invoice_number or f"MR-{receipt.id}",
            "invoice_date": receipt.invoice_date.strftime("%Y-%m-%d") if receipt.invoice_date else None,
            "vendor_name": getattr(receipt, "vendor_name", None) or po.supplier,
            "created_by": getattr(receipt, "created_by", None) or "",
            "payment_status": getattr(receipt, "payment_status", "Unpaid") or "Unpaid",
            "freight_charge": float(getattr(receipt, "freight_charge", 0) or 0),
            "lines": receipt_lines,
        })

    return {
        "po_id": po.id,
        "order_number": po.order_number,
        "supplier": po.supplier,
        "item_name": po.item_name,
        "status": po.status,
        "po_type": getattr(po, "po_type", "Regular Vendor PO") or "Regular Vendor PO",
        "created_by": getattr(po, "created_by", None) or "",
        "po_date": po_date,
        "grand_total": float(details.grand_total if details else 0),
        "gst_rate": float(details.gst_rate if details else 18),
        "freight_charges": float(getattr(details, "freight_charges", 0) or 0),
        "remark_1": getattr(details, "remark_1", None) or "",
        "remark_2": getattr(details, "remark_2", None) or "",
        "lines": lines_out,
        "receipts": receipts_out,
    }


# ── Marketing Accounts: Sales, Work Order Sales, and Credit Notes ─────────────
@router.get("/marketing")
def get_marketing_accounts(user: User = Depends(accounts_user), db: Session = Depends(get_db)):
    """
    Returns verified financial transactions and invoices from Marketing Workspace:
    1. Marketing Sales (PO Invoices)
    2. Work Order Sales (WO Invoices)
    3. Credit Notes
    """
    sales = (
        db.query(Sale)
        .filter(Sale.is_deleted == False)
        .options(
            joinedload(Sale.items),
            joinedload(Sale.purchase_order).joinedload(PurchaseOrder.line_items)
        )
        .order_by(Sale.invoice_date.desc(), Sale.id.desc())
        .all()
    )

    wo_sales = (
        db.query(WorkOrderSale)
        .filter(WorkOrderSale.is_deleted == False)
        .options(
            joinedload(WorkOrderSale.items),
            joinedload(WorkOrderSale.work_order).joinedload(WorkOrder.line_items)
        )
        .order_by(WorkOrderSale.invoice_date.desc(), WorkOrderSale.id.desc())
        .all()
    )

    credit_notes = (
        db.query(CreditNote)
        .filter(CreditNote.is_deleted == False)
        .options(joinedload(CreditNote.items))
        .order_by(CreditNote.cn_date.desc(), CreditNote.id.desc())
        .all()
    )

    sales_out = []
    for s in sales:
        po = s.purchase_order
        if not po and s.po_id:
            po = db.query(PurchaseOrder).filter(PurchaseOrder.id == s.po_id).first()
        if not po and s.po_number:
            po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == s.po_number, PurchaseOrder.is_deleted == False).first()
        po_qty = float(po.total_qty or 0) if po else 0.0
        dispatch_qty = sum(float(it.quantity or 0) for it in (s.items or []))

        po_li_by_id = {}
        po_li_by_name = {}
        if po and po.line_items:
            for li in po.line_items:
                po_li_by_id[li.id] = float(li.quantity or 0)
                if li.item:
                    po_li_by_name[li.item.strip().lower()] = float(li.quantity or 0)

        items_out = []
        for it in (s.items or []):
            item_po_qty = 0.0
            if it.line_item_id and it.line_item_id in po_li_by_id:
                item_po_qty = po_li_by_id[it.line_item_id]
            elif it.item and it.item.strip().lower() in po_li_by_name:
                item_po_qty = po_li_by_name[it.item.strip().lower()]
            elif po and len(s.items or []) == 1:
                item_po_qty = po_qty

            items_out.append({
                "id": it.id,
                "item": it.item,
                "uom": it.uom or "Nos",
                "po_qty": item_po_qty,
                "dispatch_qty": float(it.quantity or 0),
                "quantity": float(it.quantity or 0),
                "unit_price": float(it.unit_price or 0),
                "gst_rate": float(it.gst_rate or 0),
                "hsn_sac": getattr(it, "hsn_sac", None) or getattr(s, "hsn_code", None) or "",
                "total_amount": float(it.total_amount or 0),
            })

        taxable_amount, gst_amount = compute_sale_taxable_and_gst(s.items or [])
        freight = float(s.freight or 0)
        grand_total = compute_sale_grand_total(taxable_amount, gst_amount, freight)

        sales_out.append({
            "id": s.id,
            "po_id": s.po_id,
            "po_number": s.po_number or "—",
            "po_date": po.po_date.strftime("%Y-%m-%d") if po and po.po_date else None,
            "invoice_number": s.invoice_number or "—",
            "invoice_date": s.invoice_date.strftime("%Y-%m-%d") if s.invoice_date else (s.created_at.strftime("%Y-%m-%d") if s.created_at else "—"),
            "client_name": s.client_name or "—",
            "project": s.project or "—",
            "po_qty": po_qty,
            "dispatch_qty": dispatch_qty,
            "subtotal": taxable_amount,
            "gst_amount": gst_amount,
            "freight": freight,
            "grand_total": grand_total,
            "payment_status": str(s.payment_status.value if hasattr(s.payment_status, "value") else s.payment_status) if s.payment_status else "Pending",
            "payment_note": s.payment_note or "",
            "payment_terms": s.payment_terms or "",
            "delivery_status": s.delivery_status or "Not Delivered",
            "created_by": s.created_by or "",
            "items": items_out,
            "items_count": len(items_out),
            "items_summary": ", ".join(it["item"] for it in items_out) if items_out else "—",
        })

    wo_sales_out = []
    for ws in wo_sales:
        wo = ws.work_order
        if not wo and ws.wo_id:
            wo = db.query(WorkOrder).filter(WorkOrder.id == ws.wo_id).first()
        if not wo and ws.wo_number:
            wo = db.query(WorkOrder).filter(WorkOrder.wo_number == ws.wo_number, WorkOrder.is_deleted == False).first()
        wo_qty = float(wo.total_qty or 0) if wo else 0.0
        dispatch_qty = sum(float(it.quantity or 0) for it in (ws.items or []))

        wo_li_by_id = {}
        wo_li_by_name = {}
        if wo and wo.line_items:
            for li in wo.line_items:
                wo_li_by_id[li.id] = float(li.quantity or 0)
                if li.item:
                    wo_li_by_name[li.item.strip().lower()] = float(li.quantity or 0)

        items_out = []
        for it in (ws.items or []):
            item_wo_qty = 0.0
            if it.line_item_id and it.line_item_id in wo_li_by_id:
                item_wo_qty = wo_li_by_id[it.line_item_id]
            elif it.item and it.item.strip().lower() in wo_li_by_name:
                item_wo_qty = wo_li_by_name[it.item.strip().lower()]
            elif wo and len(ws.items or []) == 1:
                item_wo_qty = wo_qty

            items_out.append({
                "id": it.id,
                "item": it.item,
                "uom": it.uom or "Nos",
                "po_qty": item_wo_qty,
                "dispatch_qty": float(it.quantity or 0),
                "quantity": float(it.quantity or 0),
                "unit_price": float(it.unit_price or 0),
                "gst_rate": float(it.gst_rate or 0),
                "hsn_sac": getattr(it, "hsn_sac", None) or getattr(ws, "hsn_code", None) or "",
                "total_amount": float(it.total_amount or 0),
            })

        wo_taxable, wo_gst = compute_sale_taxable_and_gst(ws.items or [])
        wo_freight = float(ws.freight or 0)
        wo_grand = compute_sale_grand_total(wo_taxable, wo_gst, wo_freight)

        wo_sales_out.append({
            "id": ws.id,
            "wo_id": ws.wo_id,
            "wo_number": ws.wo_number or "—",
            "wo_date": wo.wo_date.strftime("%Y-%m-%d") if wo and wo.wo_date else None,
            "invoice_number": ws.invoice_number or "—",
            "invoice_date": ws.invoice_date.strftime("%Y-%m-%d") if ws.invoice_date else (ws.created_at.strftime("%Y-%m-%d") if ws.created_at else "—"),
            "client_name": ws.client_name or "—",
            "project": ws.project or "—",
            "po_qty": wo_qty,
            "dispatch_qty": dispatch_qty,
            "subtotal": wo_taxable,
            "gst_amount": wo_gst,
            "freight": wo_freight,
            "grand_total": wo_grand,
            "payment_status": str(ws.payment_status.value if hasattr(ws.payment_status, "value") else ws.payment_status) if ws.payment_status else "Pending",
            "payment_note": ws.payment_note or "",
            "payment_terms": ws.payment_terms or "",
            "delivery_status": ws.delivery_status or "Not Delivered",
            "created_by": ws.created_by or "",
            "items": items_out,
            "items_count": len(items_out),
            "items_summary": ", ".join(it["item"] for it in items_out) if items_out else "—",
        })

    cn_out = []
    for cn in credit_notes:
        items_out = [
            {
                "id": it.id,
                "item": getattr(it, "item", None) or getattr(it, "item_description", "—"),
                "uom": it.uom or "Nos",
                "original_qty": float(it.original_qty or 0),
                "credit_qty": float(it.credit_qty or 0),
                "unit_price": float(getattr(it, "unit_price", 0) or getattr(it, "unit_rate", 0) or 0),
                "gst_rate": float(it.gst_rate or 0),
                "taxable_amount": float(getattr(it, "subtotal", 0) or getattr(it, "taxable_amount", 0) or 0),
                "gst_amount": float(it.gst_amount or 0),
                "total_amount": float(it.total_amount or 0),
            }
            for it in (cn.items or [])
        ]
        cn_out.append({
            "id": cn.id,
            "cn_number": cn.cn_number,
            "cn_date": cn.cn_date.strftime("%Y-%m-%d") if cn.cn_date else "—",
            "sale_type": cn.sale_type or "PO",
            "sale_id": cn.sale_id,
            "wo_sale_id": cn.wo_sale_id,
            "invoice_number": cn.invoice_number or "—",
            "invoice_date": cn.invoice_date.strftime("%Y-%m-%d") if cn.invoice_date else "—",
            "po_number": cn.po_number or "—",
            "client_name": cn.client_name or "—",
            "project": cn.project or "—",
            "reason": cn.reason or "—",
            "taxable_amount": float(cn.taxable_amount or 0),
            "gst_amount": float(cn.gst_amount or 0),
            "total_amount": float(cn.total_amount or 0),
            "status": cn.status or "Issued",
            "created_by": cn.created_by or "",
            "items": items_out,
            "items_count": len(items_out),
            "items_summary": ", ".join(it["item"] for it in items_out) if items_out else "—",
        })

    total_sales_amt = round(sum(s["grand_total"] for s in sales_out), 2)
    total_wo_sales_amt = round(sum(ws["grand_total"] for ws in wo_sales_out), 2)
    total_cn_amt = round(sum(float(c["total_amount"] or 0) for c in cn_out if c["status"] != "Cancelled"), 2)
    net_invoiced_amt = round(total_sales_amt + total_wo_sales_amt - total_cn_amt, 2)

    return {
        "summary": {
            "sales_count": len(sales_out),
            "sales_amount": total_sales_amt,
            "wo_sales_count": len(wo_sales_out),
            "wo_sales_amount": total_wo_sales_amt,
            "credit_notes_count": len(cn_out),
            "credit_notes_amount": total_cn_amt,
            "net_revenue": net_invoiced_amt,
        },
        "sales": sales_out,
        "wo_sales": wo_sales_out,
        "credit_notes": cn_out,
    }
