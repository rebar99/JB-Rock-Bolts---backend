from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Query
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime
import os
import uuid
from pydantic import BaseModel
from app.database import get_db
from app.utils.helpers import generate_invoice_number, compute_sale_financials, log_activity
from app.routers.upload_helpers import (
    read_upload_bytes, save_upload_bytes, parse_import_file,
    parse_optional_datetime, make_excel_response, style_header_row,
)
from app.models.models import Sale, SaleActivity, PurchaseOrder, SaleItem, POLineItem
from app.schemas.sale import SaleCreate, SaleUpdate, SaleOut, SaleActivityCreate, SaleActivityOut, SaleItemCreate

router = APIRouter(prefix="/api/sales", tags=["Sales"])


class MarkDeliveredPayload(BaseModel):
    delivery_challan_url: str
    updated_by: Optional[str] = None


# ── Helper ────────────────────────────────────────────────────────────────────

def _float(value) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("₹", "").replace("%", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    return float(value)


def _field(row: Dict[str, Any], keys):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


# ── List ──────────────────────────────────────────────────────────────────────

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


# ── File upload ───────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_invoice_file(file: UploadFile = File(...)):
    content = await read_upload_bytes(file)
    file_url = save_upload_bytes(content, file.filename)
    return {"file_url": file_url}


# ── Excel export ──────────────────────────────────────────────────────────────

@router.get("/export")
def export_sales(db: Session = Depends(get_db)):
    """Export all Sales / Invoices to an Excel (.xlsx) file."""
    from openpyxl import Workbook

    sales = db.query(Sale).order_by(Sale.created_at.desc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"

    headers = [
        "Invoice Number", "PO Number", "Client Name", "Project",
        "Item Name", "UOM", "Quantity", "Unit Price", "GST Rate (%)", "HSN/SAC",
        "Subtotal", "GST Amount", "Freight", "Grand Total",
        "Payment Status", "Payment Terms",
        "Dispatch From", "Ship To", "Bill To", "Dispatched Through",
        "E-Way Bill No", "Buyer's Order No",
        "Invoice Document URL", "E-Way Bill Document URL",
        "Created By",
    ]
    ws.append(headers)
    style_header_row(ws, len(headers))

    for s in sales:
        items = s.items if s.items else [None]
        for si in items:
            ws.append([
                s.invoice_number or "",
                s.po_number or "",
                s.client_name or "",
                s.project or "",
                si.item if si else "",
                si.uom if si else "",
                float(si.quantity or 0) if si else 0,
                float(si.unit_price or 0) if si else 0,
                float(si.gst_rate or 0) if si else 0,
                s.hsn_code or "",
                float(si.subtotal or 0) if si else float(s.subtotal or 0),
                float(si.gst_amount or 0) if si else float(s.gst_amount or 0),
                float(s.freight or 0),
                float(s.grand_total or 0),
                s.payment_status.value if hasattr(s.payment_status, "value") else str(s.payment_status or ""),
                s.payment_terms or "",
                s.dispatch_from or "",
                s.ship_to or "",
                s.bill_to or "",
                s.dispatched_through or "",
                s.e_way_bill_no or "",
                s.buyers_order_no or "",
                s.invoice_url or "",
                s.e_way_bill_url or "",
                s.created_by or "",
            ])

    return make_excel_response(wb, "sales-export.xlsx")


# ── Excel import ──────────────────────────────────────────────────────────────

@router.post("/import")
async def import_sales(
    file: UploadFile = File(...),
    on_conflict: str = Query("skip", description="skip | update"),
    created_by: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Import Sales from an Excel (.xlsx) or CSV file.

    on_conflict:
      skip   – leave existing invoices untouched (default)
      update – update header/payment fields of existing invoices
    """
    raw_rows = parse_import_file(await read_upload_bytes(file), file.filename)
    grouped: Dict[str, Dict[str, Any]] = {}

    for row_idx, row in enumerate(raw_rows):
        invoice_number = _field(row, ["invoice_number", "Invoice Number", "Invoice No", "invoice", "inv", "invoice_no"])
        po_number = _field(row, ["po_number", "PO Number", "PO No", "po", "po_no"])
        key = str((invoice_number or po_number or f"row-{row_idx}")).strip()
        if key not in grouped:
            grouped[key] = {"base": row, "rows": []}
        grouped[key]["rows"].append(row)

    created = updated = skipped = 0
    errors = []

    for group_key, group in grouped.items():
        base = group["base"]
        rows = group["rows"]

        po_number = _field(base, ["po_number", "PO Number", "PO No", "po", "po_no"])
        if not po_number:
            errors.append(f"Invoice group {group_key}: missing PO number – skipped")
            continue
        po_number = str(po_number).strip()

        po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == po_number).first()
        if not po:
            errors.append(f"Invoice {group_key}: PO {po_number} not found – skipped")
            continue

        invoice_number = str(_field(base, ["invoice_number", "Invoice Number", "Invoice No", "invoice", "inv", "invoice_no"]) or generate_invoice_number(db)).strip()

        # Check for existing sale by invoice_number
        existing = db.query(Sale).filter(Sale.invoice_number == invoice_number).first()
        if existing:
            if on_conflict == "skip":
                skipped += 1
                continue
            elif on_conflict == "update":
                # Update header/payment fields only
                ps = _field(base, ["payment_status", "Payment Status", "status"])
                if ps:
                    existing.payment_status = ps
                pn = _field(base, ["payment_note", "note"])
                if pn:
                    existing.payment_note = str(pn)
                ship = _field(base, ["ship_to", "Ship To"])
                if ship:
                    existing.ship_to = str(ship)
                bill = _field(base, ["bill_to", "Bill To"])
                if bill:
                    existing.bill_to = str(bill)
                eway_no = _field(base, ["e_way_bill_no", "E-Way Bill No", "eway_bill_no"])
                if eway_no:
                    existing.e_way_bill_no = str(eway_no)
                buyers = _field(base, ["buyers_order_no", "Buyer's Order No", "buyers_order"])
                if buyers:
                    existing.buyers_order_no = str(buyers)
                existing.updated_by = created_by or "Import"
                existing.updated_at = datetime.utcnow()
                try:
                    db.commit()
                    updated += 1
                except Exception as e:
                    db.rollback()
                    errors.append(f"Invoice {invoice_number}: update failed – {str(e)}")
                continue

        # Build items
        client_name = str(_field(base, ["client_name", "Client Name", "client", "customer"]) or po.client_name)
        project = str(_field(base, ["project", "Project", "project_name"]) or po.project or "")
        payment_terms = str(_field(base, ["payment_terms", "Payment Terms", "terms"]) or po.payment_terms or "")
        hsn_code = str(_field(base, ["hsn_code", "HSN/SAC", "hsn"]) or "")
        freight = _float(_field(base, ["freight", "Freight", "shipping_charge", "freight_amount"]))
        invoice_url = str(_field(base, ["invoice_url", "Invoice Document URL", "invoice_file", "file_url"]) or "")
        e_way_bill_url = str(_field(base, ["e_way_bill_url", "E-Way Bill Document URL", "eway_bill_url"]) or "")
        dispatch_from = str(_field(base, ["dispatch_from", "Dispatch From"]) or "")
        ship_to = str(_field(base, ["ship_to", "Ship To"]) or "")
        bill_to = str(_field(base, ["bill_to", "Bill To"]) or "")
        dispatched_through = str(_field(base, ["dispatched_through", "Dispatched Through", "dispatched_via"]) or "")
        e_way_bill_no = str(_field(base, ["e_way_bill_no", "E-Way Bill No", "eway_bill_no"]) or "")
        buyers_order_no = str(_field(base, ["buyers_order_no", "Buyer's Order No", "buyers_order"]) or "")
        payment_status = str(_field(base, ["payment_status", "Payment Status", "status"]) or "Pending")
        payment_note = str(_field(base, ["payment_note", "note"]) or "")

        items = []
        for row in rows:
            item_name = _field(row, ["item", "Item Name", "product", "description"])
            quantity = _float(_field(row, ["quantity", "Quantity", "qty", "quantity_dispatched", "dispatch_quantity"]))
            if not item_name or quantity <= 0:
                continue
            unit_price = _float(_field(row, ["unit_price", "Unit Price", "price", "rate"]))
            gst_rate = _float(_field(row, ["gst_rate", "GST Rate (%)", "gst", "tax_rate"]))
            subtotal = _float(_field(row, ["subtotal", "Subtotal", "sub_total", "amount"])) or quantity * unit_price
            gst_amount = _float(_field(row, ["gst_amount", "GST Amount", "tax_amount"])) or round(subtotal * gst_rate / 100, 2)
            total_amount = _float(_field(row, ["total_amount", "line_total", "total"])) or subtotal + gst_amount
            line_item_id = _field(row, ["line_item_id", "po_line_item_id", "item_id"])
            items.append({
                "line_item_id": int(line_item_id) if line_item_id not in (None, "") and str(line_item_id).isdigit() else None,
                "item": item_name,
                "uom": str(_field(row, ["uom", "UOM", "unit"]) or "Nos"),
                "quantity": quantity,
                "unit_price": unit_price,
                "gst_rate": gst_rate,
                "subtotal": subtotal,
                "gst_amount": gst_amount,
                "total_amount": total_amount,
            })

        if not items:
            item_name = _field(base, ["item", "Item Name", "product", "description"])
            quantity = _float(_field(base, ["quantity", "Quantity", "qty", "quantity_dispatched"]))
            if item_name and quantity > 0:
                unit_price = _float(_field(base, ["unit_price", "Unit Price", "price", "rate"]))
                gst_rate = _float(_field(base, ["gst_rate", "GST Rate (%)", "gst", "tax_rate"]))
                subtotal = _float(_field(base, ["subtotal", "Subtotal"])) or quantity * unit_price
                gst_amount = _float(_field(base, ["gst_amount", "GST Amount"])) or round(subtotal * gst_rate / 100, 2)
                total_amount = subtotal + gst_amount
                li_id_raw = _field(base, ["line_item_id", "po_line_item_id"])
                items.append({
                    "line_item_id": int(li_id_raw) if li_id_raw not in (None, "") and str(li_id_raw).isdigit() else None,
                    "item": item_name,
                    "uom": str(_field(base, ["uom", "UOM", "unit"]) or "Nos"),
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "gst_rate": gst_rate,
                    "subtotal": subtotal,
                    "gst_amount": gst_amount,
                    "total_amount": total_amount,
                })

        if not items:
            errors.append(f"Invoice {invoice_number} on PO {po_number}: no valid line items – skipped")
            continue

        subtotal_total = _float(_field(base, ["subtotal", "Subtotal"])) or sum(i["subtotal"] for i in items)
        gst_total = _float(_field(base, ["gst_amount", "GST Amount"])) or sum(i["gst_amount"] for i in items)
        grand_total = _float(_field(base, ["grand_total", "Grand Total", "total", "invoice_total"])) or subtotal_total + gst_total + freight

        try:
            payload = SaleCreate(
                po_id=po.id,
                po_number=po_number,
                invoice_number=invoice_number,
                client_name=client_name,
                project=project,
                items=[SaleItemCreate(**item) for item in items],
                subtotal=subtotal_total,
                gst_amount=gst_total,
                freight=freight,
                grand_total=grand_total,
                payment_status=payment_status,
                payment_note=payment_note,
                invoice_url=invoice_url,
                e_way_bill_url=e_way_bill_url,
                dispatch_from=dispatch_from,
                ship_to=ship_to,
                bill_to=bill_to,
                dispatched_through=dispatched_through,
                e_way_bill_no=e_way_bill_no,
                buyers_order_no=buyers_order_no,
                payment_terms=payment_terms,
                hsn_code=hsn_code,
                created_by=created_by or "Import",
            )
            create_sale(payload, db=db)
            created += 1
        except HTTPException as e:
            errors.append(f"Invoice {invoice_number}: {e.detail}")
        except Exception as e:
            errors.append(f"Invoice {invoice_number}: {str(e)}")

    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors}


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("", response_model=SaleOut, status_code=status.HTTP_201_CREATED)
def create_sale(payload: SaleCreate, db: Session = Depends(get_db)):
    po = db.get(PurchaseOrder, payload.po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found.")

    if po.short_closed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot create a dispatch or invoice against a Short Closed Purchase Order."
        )

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

        if po and po.short_closed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot modify dispatch items for a Short Closed Purchase Order."
            )

        for old_item in sale.items:
            if po:
                po.delivered_quantity = max(0, po.delivered_quantity - old_item.quantity)
            if old_item.line_item_id:
                li = db.get(POLineItem, old_item.line_item_id)
                if li:
                    li.delivered_quantity = max(0, li.delivered_quantity - old_item.quantity)

        db.query(SaleItem).filter(SaleItem.sale_id == sale.id).delete()

        for item_data in new_items_data:
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
                total_amount=item_data.get("total_amount", 0),
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
