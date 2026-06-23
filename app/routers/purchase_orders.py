from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional, Dict, Any
from datetime import datetime
from app.database import get_db
from app.models.models import PurchaseOrder, POLineItem
from app.schemas.purchase_order import PurchaseOrderCreate, PurchaseOrderUpdate, PurchaseOrderOut
from app.utils.helpers import log_activity
from app.routers.upload_helpers import (
    read_upload_bytes, save_upload_bytes, parse_import_file,
    parse_optional_datetime, make_excel_response, style_header_row,
)

router = APIRouter(prefix="/api/purchase-orders", tags=["Purchase Orders"])


# ── Helper ────────────────────────────────────────────────────────────────────

def _float(value) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("₹", "").strip()
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


# ── File upload ───────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_po_file(file: UploadFile = File(...)):
    content = await read_upload_bytes(file)
    file_url = save_upload_bytes(content, file.filename)
    return {"file_url": file_url}


# ── Excel export ──────────────────────────────────────────────────────────────

@router.get("/export")
def export_purchase_orders(db: Session = Depends(get_db)):
    """Export all Purchase Orders to an Excel (.xlsx) file."""
    from openpyxl import Workbook

    orders = db.query(PurchaseOrder).order_by(PurchaseOrder.created_at.desc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Purchase Orders"

    headers = [
        "PO Number", "Client Name", "Project",
        "Item Name", "Quantity", "UOM", "Unit Price",
        "GST", "Freight", "Subtotal", "Row Total",
        "Payment Terms", "PO Validity Date", "PO Document URL", "Created By",
    ]
    ws.append(headers)
    style_header_row(ws, len(headers))

    for o in orders:
        items = o.line_items if o.line_items else [None]
        for li in items:
            if li:
                qty = float(li.quantity or 0)
                price = float(li.unit_price or 0)
                sub = qty * price
                gst_str = str(li.gst or "0")
                gst_pct = _float(gst_str.replace("₹", "").replace("%", ""))
                gst_amt = _float(gst_str.replace("%", "")) if gst_str.startswith("₹") else sub * gst_pct / 100
                freight = float(li.freight or 0)
                row_total = sub + gst_amt + freight
                ws.append([
                    o.po_number, o.client_name, o.project or "",
                    li.item, qty, li.uom or "Nos", price,
                    gst_str, freight, round(sub, 2), round(row_total, 2),
                    o.payment_terms or "",
                    o.validity_date.strftime("%Y-%m-%d") if o.validity_date else "",
                    o.file_url or "",
                    o.created_by or "",
                ])
            else:
                qty = float(o.total_quantity or 0)
                price = float(o.unit_price or 0)
                sub = qty * price
                gst_str = str(o.gst or "0")
                gst_pct = _float(gst_str.replace("₹", "").replace("%", ""))
                gst_amt = _float(gst_str.replace("%", "")) if gst_str.startswith("₹") else sub * gst_pct / 100
                freight = float(o.freight or 0)
                row_total = sub + gst_amt + freight
                ws.append([
                    o.po_number, o.client_name, o.project or "",
                    o.item or "", qty, o.uom or "Nos", price,
                    gst_str, freight, round(sub, 2), round(row_total, 2),
                    o.payment_terms or "",
                    o.validity_date.strftime("%Y-%m-%d") if o.validity_date else "",
                    o.file_url or "",
                    o.created_by or "",
                ])

    return make_excel_response(wb, "purchase-orders-export.xlsx")


# ── Excel import ──────────────────────────────────────────────────────────────

@router.post("/import")
async def import_purchase_orders(
    file: UploadFile = File(...),
    on_conflict: str = Query("skip", description="skip | update"),
    created_by: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Import Purchase Orders from an Excel (.xlsx) or CSV file.

    on_conflict:
      skip   – leave existing POs untouched (default)
      update – update header fields of existing POs (line items preserved)
    """
    raw_rows = parse_import_file(await read_upload_bytes(file), file.filename)
    grouped: Dict[str, Dict[str, Any]] = {}

    for row in raw_rows:
        po_number = _field(row, ["po_number", "PO Number", "po", "po_no"])
        if not po_number:
            continue
        key = str(po_number).strip()
        if key not in grouped:
            grouped[key] = {"base": row, "rows": []}
        grouped[key]["rows"].append(row)

    created = updated = skipped = 0
    errors = []

    for po_number, group in grouped.items():
        base = group["base"]
        rows = group["rows"]

        client_name = _field(base, ["client_name", "Client Name", "client", "customer"])
        if not client_name:
            errors.append(f"PO {po_number}: missing client name – skipped")
            continue

        # Build line items
        if "line_items" in base and isinstance(base["line_items"], list):
            line_items_raw = base["line_items"]
        elif "items" in base and isinstance(base["items"], list):
            line_items_raw = base["items"]
        else:
            line_items_raw = []
            for row in rows:
                item_name = _field(row, ["item", "Item Name", "product", "description"])
                if not item_name:
                    continue
                line_items_raw.append({
                    "item": item_name,
                    "quantity": _float(_field(row, ["quantity", "Quantity", "qty", "total_quantity"])),
                    "uom": _field(row, ["uom", "UOM", "unit"]) or "Nos",
                    "unit_price": _float(_field(row, ["unit_price", "Unit Price", "price", "rate"])),
                    "gst": str(_field(row, ["gst", "GST", "gst_rate"]) or "0"),
                    "freight": _float(_field(row, ["freight", "Freight", "line_freight"])),
                })

        if not line_items_raw:
            item_name = _field(base, ["item", "Item Name", "product", "description"])
            if item_name:
                line_items_raw = [{
                    "item": item_name,
                    "quantity": _float(_field(base, ["quantity", "Quantity", "qty", "total_quantity"])),
                    "uom": _field(base, ["uom", "UOM", "unit"]) or "Nos",
                    "unit_price": _float(_field(base, ["unit_price", "Unit Price", "price", "rate"])),
                    "gst": str(_field(base, ["gst", "GST", "gst_rate"]) or "0"),
                    "freight": _float(_field(base, ["freight", "Freight", "line_freight"])),
                }]

        # Check for existing PO
        existing = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == po_number).first()

        if existing:
            if on_conflict == "skip":
                skipped += 1
                continue
            elif on_conflict == "update":
                # Update header fields only (don't touch line items linked to Sales)
                existing.client_name = str(client_name)
                existing.project = str(_field(base, ["project", "Project", "project_name"]) or existing.project or "")
                existing.payment_terms = str(_field(base, ["payment_terms", "Payment Terms", "terms"]) or existing.payment_terms or "")
                validity = parse_optional_datetime(_field(base, ["validity_date", "PO Validity Date", "validity", "po_date"]))
                if validity:
                    existing.validity_date = validity
                gst_val = _field(base, ["gst", "GST", "gst_rate"])
                if gst_val:
                    existing.gst = str(gst_val)
                freight_val = _field(base, ["freight", "Freight", "shipping_charge"])
                if freight_val not in (None, ""):
                    existing.freight = _float(freight_val)
                existing.last_updated_at = datetime.utcnow()
                existing.last_updated_by = created_by or "Import"
                try:
                    db.commit()
                    updated += 1
                except Exception as e:
                    db.rollback()
                    errors.append(f"PO {po_number}: update failed – {str(e)}")
                continue

        # Create new PO
        po_payload = {
            "client_name": str(client_name),
            "po_number": po_number,
            "project": str(_field(base, ["project", "Project", "project_name"]) or ""),
            "location": str(_field(base, ["location", "ship_to", "shipping_address"]) or ""),
            "gst": str(_field(base, ["gst", "GST", "gst_rate"]) or "0"),
            "freight": _float(_field(base, ["freight", "Freight", "shipping_charge", "freight_amount"])),
            "payment_terms": str(_field(base, ["payment_terms", "Payment Terms", "terms"]) or ""),
            "validity_date": parse_optional_datetime(_field(base, ["validity_date", "PO Validity Date", "validity", "po_date"])),
            "created_by": created_by or "Import",
            "line_items": line_items_raw,
        }

        try:
            payload = PurchaseOrderCreate(**po_payload)
            create_purchase_order(payload, db=db)
            created += 1
        except HTTPException as e:
            errors.append(f"PO {po_number}: {e.detail}")
        except Exception as e:
            errors.append(f"PO {po_number}: {str(e)}")

    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors}


# ── CRUD ──────────────────────────────────────────────────────────────────────

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

    if payload.line_items:
        first = payload.line_items[0]
        data["item"] = first.item
        data["uom"] = first.uom
        data["unit_price"] = first.unit_price
        data["total_quantity"] = sum(li.quantity for li in payload.line_items)

    po = PurchaseOrder(**data)

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
                    freight=li_data.freight,
                )
                new_line_items.append(new_li)

        po.line_items = new_line_items

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
