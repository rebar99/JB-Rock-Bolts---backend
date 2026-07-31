from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Query
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional, Dict, Any
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel
from app.database import get_db
from app.utils.helpers import (
    generate_wo_invoice_number, log_activity, recalc_wo_completed_quantities,
    compute_line_taxable_and_gst, compute_sale_taxable_and_gst, compute_sale_grand_total,
)
from app.routers.upload_helpers import (
    read_upload_bytes, save_upload_bytes, parse_import_file,
    parse_optional_datetime, make_excel_response, style_header_row,
)
from app.models.models import WorkOrderSale, WorkOrderSaleActivity, WorkOrder, WorkOrderSaleItem, WorkOrderSaleDispatch, WorkOrderSaleDispatchItem
from app.schemas.work_order_sale import (
    WorkOrderSaleCreate, WorkOrderSaleUpdate, WorkOrderSaleOut, WorkOrderSaleActivityCreate, WorkOrderSaleActivityOut, WorkOrderSaleItemCreate,
    WorkOrderSaleDispatchCreate, WorkOrderSaleDispatchOut,
)

router = APIRouter(prefix="/api/work-order-sales", tags=["Work Order Sales"])

CLOSED_STATUSES = ("Closed", "Cancelled")


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

@router.get("", response_model=List[WorkOrderSaleOut])
def list_work_order_sales(
    wo_id: Optional[int] = None,
    client: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    q = db.query(WorkOrderSale)
    if wo_id:
        q = q.filter(WorkOrderSale.wo_id == wo_id)
    if client:
        q = q.filter(WorkOrderSale.client_name.ilike(f"%{client}%"))
    return q.order_by(WorkOrderSale.created_at.desc()).offset(skip).limit(limit).all()


# ── File upload ───────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_wo_invoice_file(file: UploadFile = File(...)):
    content = await read_upload_bytes(file)
    file_url = save_upload_bytes(content, file.filename)
    return {"file_url": file_url}


# ── Excel export ──────────────────────────────────────────────────────────────

@router.get("/export")
def export_work_order_sales(db: Session = Depends(get_db)):
    """Export all Work Order Sales / Invoices to an Excel (.xlsx) file."""
    from openpyxl import Workbook

    sales = db.query(WorkOrderSale).options(joinedload(WorkOrderSale.items)).order_by(WorkOrderSale.created_at.desc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Work Order Sales"

    headers = [
        "Invoice Number", "WO Number", "Client Name", "Project",
        "Item Name", "UOM", "Quantity", "Unit Price", "GST Rate (%)", "HSN/SAC",
        "Subtotal", "GST Amount", "Freight", "Grand Total",
        "Payment Status", "Payment Note", "Payment Terms",
        "Dispatch From", "Ship To", "Bill To", "Dispatched Through",
        "E-Way Bill No", "Buyer's Order No",
        "Invoice Document URL", "E-Way Bill Document URL",
        "Created By",
    ]
    ws.append(headers)
    style_header_row(ws, len(headers))

    for s in sales:
        s_taxable, s_gst = compute_sale_taxable_and_gst(s.items)
        s_grand_total = compute_sale_grand_total(s_taxable, s_gst, float(s.freight or 0))
        items = s.items if s.items else [None]
        for si in items:
            si_taxable, si_gst = compute_line_taxable_and_gst(si.quantity, si.unit_price, si.gst_rate) if si else (0.0, 0.0)
            ws.append([
                s.invoice_number or "",
                s.wo_number or "",
                s.client_name or "",
                s.project or "",
                si.item if si else "",
                si.uom if si else "",
                float(si.quantity or 0) if si else 0,
                float(si.unit_price or 0) if si else 0,
                float(si.gst_rate or 0) if si else 0,
                s.hsn_code or "",
                si_taxable if si else s_taxable,
                si_gst if si else s_gst,
                float(s.freight or 0),
                s_grand_total,
                s.payment_status.value if hasattr(s.payment_status, "value") else str(s.payment_status or ""),
                s.payment_note or "",
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

    return make_excel_response(wb, "work-order-sales-export.xlsx")


# ── Excel import ──────────────────────────────────────────────────────────────

@router.post("/import")
async def import_work_order_sales(
    file: UploadFile = File(...),
    on_conflict: str = Query("skip", description="skip | update"),
    created_by: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Import Work Order Sales from an Excel (.xlsx) or CSV file.

    on_conflict:
      skip   – leave existing invoices untouched (default)
      update – update header/payment fields of existing invoices
    """
    raw_rows = parse_import_file(await read_upload_bytes(file), file.filename)
    grouped: Dict[str, Dict[str, Any]] = {}

    for row_idx, row in enumerate(raw_rows):
        invoice_number = _field(row, ["invoice_number", "Invoice Number", "Invoice No", "invoice", "inv", "invoice_no"])
        wo_number = _field(row, ["wo_number", "WO Number", "WO No", "wo", "wo_no"])
        key = str((invoice_number or wo_number or f"row-{row_idx}")).strip()
        if key not in grouped:
            grouped[key] = {"base": row, "rows": []}
        grouped[key]["rows"].append(row)

    created = updated = skipped = 0
    errors = []

    for group_key, group in grouped.items():
        base = group["base"]
        rows = group["rows"]

        wo_number = _field(base, ["wo_number", "WO Number", "WO No", "wo", "wo_no"])
        if not wo_number:
            errors.append(f"Invoice group {group_key}: missing WO number – skipped")
            continue
        wo_number = str(wo_number).strip()

        wo = db.query(WorkOrder).filter(WorkOrder.wo_number == wo_number).first()
        if not wo:
            errors.append(f"Invoice {group_key}: WO {wo_number} not found – skipped")
            continue

        invoice_number = str(_field(base, ["invoice_number", "Invoice Number", "Invoice No", "invoice", "inv", "invoice_no"]) or generate_wo_invoice_number(db)).strip()

        existing = db.query(WorkOrderSale).filter(WorkOrderSale.invoice_number == invoice_number).first()
        if existing:
            if on_conflict == "skip":
                skipped += 1
                continue
            elif on_conflict == "update":
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

        client_name = str(_field(base, ["client_name", "Client Name", "client", "customer"]) or wo.client_name)
        project = str(_field(base, ["project", "Project", "project_name"]) or wo.project or "")
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
            line_item_id = _field(row, ["line_item_id", "wo_line_item_id", "item_id"])
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
                li_id_raw = _field(base, ["line_item_id", "wo_line_item_id"])
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
            errors.append(f"Invoice {invoice_number} on WO {wo_number}: no valid line items – skipped")
            continue

        subtotal_total = _float(_field(base, ["subtotal", "Subtotal"])) or sum(i["subtotal"] for i in items)
        gst_total = _float(_field(base, ["gst_amount", "GST Amount"])) or sum(i["gst_amount"] for i in items)
        grand_total = _float(_field(base, ["grand_total", "Grand Total", "total", "invoice_total"])) or subtotal_total + gst_total + freight

        try:
            payload = WorkOrderSaleCreate(
                wo_id=wo.id,
                wo_number=wo_number,
                invoice_number=invoice_number,
                client_name=client_name,
                project=project,
                items=[WorkOrderSaleItemCreate(**item) for item in items],
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
                hsn_code=hsn_code,
                created_by=created_by or "Import",
            )
            create_work_order_sale(payload, db=db)
            created += 1
        except HTTPException as e:
            errors.append(f"Invoice {invoice_number}: {e.detail}")
        except Exception as e:
            errors.append(f"Invoice {invoice_number}: {str(e)}")

    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors}


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("", response_model=WorkOrderSaleOut, status_code=status.HTTP_201_CREATED)
def create_work_order_sale(payload: WorkOrderSaleCreate, db: Session = Depends(get_db)):
    wo = db.get(WorkOrder, payload.wo_id)
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found.")

    if wo.status in CLOSED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot create a dispatch or invoice against a {wo.status} Work Order."
        )

    invoice_number = payload.invoice_number or generate_wo_invoice_number(db)

    sale = WorkOrderSale(
        wo_id=payload.wo_id,
        wo_number=payload.wo_number,
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
        sale_item = WorkOrderSaleItem(
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

        if not item_data.line_item_id:
            matching_li = next(
                (l for l in wo.line_items if l.item.lower().strip() == item_data.item.lower().strip()),
                None
            )
            if matching_li:
                sale_item.line_item_id = matching_li.id

    db.flush()
    recalc_wo_completed_quantities(db, wo)

    activity = WorkOrderSaleActivity(
        sale_id=sale.id,
        action="Sale Created",
        note=payload.payment_note,
        payment_status=payload.payment_status,
        by=payload.created_by,
    )
    db.add(activity)

    dispatch = WorkOrderSaleDispatch(
        sale_id=sale.id,
        quantity=sum(i.quantity for i in payload.items),
        uom=payload.items[0].uom if payload.items else "Nos",
        subtotal=payload.subtotal,
        gst_amount=payload.gst_amount,
        amount=payload.grand_total,
        invoice_number=sale.invoice_number,
        e_way_bill_no=payload.e_way_bill_no,
        by=payload.created_by,
    )
    db.add(dispatch)
    db.flush()
    for item_data in payload.items:
        item_taxable, item_gst = compute_line_taxable_and_gst(item_data.quantity, item_data.unit_price, item_data.gst_rate)
        db.add(WorkOrderSaleDispatchItem(
            dispatch_id=dispatch.id,
            item=item_data.item,
            uom=item_data.uom,
            quantity=item_data.quantity,
            subtotal=item_taxable,
            gst_amount=item_gst,
            amount=item_taxable + item_gst,
        ))

    db.commit()
    db.refresh(sale)

    log_activity(db, "WO Sale Created", "WorkOrderSale", f"Created sale invoice {sale.invoice_number} for {wo.client_name}.", payload.created_by, sale.id, entity_name=sale.invoice_number)
    return sale


@router.get("/{sale_id}", response_model=WorkOrderSaleOut)
def get_work_order_sale(sale_id: int, db: Session = Depends(get_db)):
    sale = db.get(WorkOrderSale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Work order sale not found.")
    return sale


@router.put("/{sale_id}", response_model=WorkOrderSaleOut)
def update_work_order_sale(sale_id: int, payload: WorkOrderSaleUpdate, db: Session = Depends(get_db)):
    sale = db.get(WorkOrderSale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Work order sale not found.")

    updates = payload.model_dump(exclude_unset=True)
    updated_by = updates.pop("updated_by", None)

    if "items" in updates:
        new_items_data = updates.pop("items")
        wo = db.get(WorkOrder, sale.wo_id)

        if wo and wo.status in CLOSED_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot modify dispatch items for a {wo.status} Work Order."
            )

        removed_item_names = {si.item for si in sale.items} - {
            item_data.get("item") for item_data in new_items_data
        }

        db.query(WorkOrderSaleItem).filter(WorkOrderSaleItem.sale_id == sale.id).delete()

        if removed_item_names:
            for d in sale.dispatches:
                if not d.items:
                    continue
                kept = [di for di in d.items if di.item not in removed_item_names]
                if len(kept) == len(d.items):
                    continue
                for di in list(d.items):
                    if di.item in removed_item_names:
                        db.delete(di)
                if not kept:
                    db.delete(d)

        for item_data in new_items_data:
            li_id = item_data.get("line_item_id") if (item_data.get("line_item_id") and item_data.get("line_item_id") > 0) else None
            new_item = WorkOrderSaleItem(
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
            if not li_id and wo:
                matching_li = next(
                    (l for l in wo.line_items if l.item.lower().strip() == (item_data.get("item") or "").lower().strip()),
                    None
                )
                if matching_li:
                    new_item.line_item_id = matching_li.id

        db.flush()
        if wo:
            recalc_wo_completed_quantities(db, wo)

    changed_fields = []
    for field, value in updates.items():
        old_val = getattr(sale, field, None)
        compare_val = float(old_val) if isinstance(old_val, Decimal) else old_val
        if compare_val != value:
            changed_fields.append(field)
            setattr(sale, field, value)

    sale.updated_by = updated_by
    sale.updated_at = datetime.utcnow()

    if "payment_status" in changed_fields or "items" in payload.model_dump(exclude_unset=True):
        action = "Sale Details Updated"
        if "payment_status" in changed_fields:
            action = "Payment Status Updated"
        activity = WorkOrderSaleActivity(
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

    log_activity(
        db, "WO Sale Updated", "WorkOrderSale", details_str,
        updated_by or "System", sale.id,
        entity_name=sale.invoice_number,
        changed_fields=", ".join(changed_fields) if changed_fields else None,
    )
    return sale


@router.put("/{sale_id}/mark-delivered", response_model=WorkOrderSaleOut)
def mark_delivered(
    sale_id: int,
    payload: MarkDeliveredPayload,
    db: Session = Depends(get_db),
):
    sale = db.get(WorkOrderSale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Work order sale not found.")

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

    activity = WorkOrderSaleActivity(
        sale_id=sale.id,
        action="Marked Delivered",
        note="Sale marked as delivered with challan document.",
        payment_status=sale.payment_status.value if hasattr(sale.payment_status, 'value') else str(sale.payment_status),
        by=payload.updated_by,
    )
    db.add(activity)
    db.commit()
    db.refresh(sale)
    log_activity(db, "WO Sale Marked Delivered", "WorkOrderSale", f"Sale invoice {sale.invoice_number} marked as Delivered.", payload.updated_by, sale.id, entity_name=sale.invoice_number)
    return sale


@router.delete("/{sale_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_work_order_sale(sale_id: int, deleted_by: Optional[str] = None, db: Session = Depends(get_db)):
    sale = db.get(WorkOrderSale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Work order sale not found.")

    wo = db.get(WorkOrder, sale.wo_id)

    db.delete(sale)
    db.flush()
    if wo:
        recalc_wo_completed_quantities(db, wo)
    db.commit()
    log_activity(db, "WO Sale Deleted", "WorkOrderSale", f"Deleted sale invoice {sale.invoice_number} for {sale.client_name}.", deleted_by or "System", sale_id, entity_name=sale.invoice_number)


@router.post("/{sale_id}/activities", response_model=WorkOrderSaleActivityOut, status_code=status.HTTP_201_CREATED)
def add_activity(sale_id: int, payload: WorkOrderSaleActivityCreate, db: Session = Depends(get_db)):
    sale = db.get(WorkOrderSale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Work order sale not found.")
    activity = WorkOrderSaleActivity(
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


@router.delete("/{sale_id}/dispatches/{dispatch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_work_order_sale_dispatch(sale_id: int, dispatch_id: int, deleted_by: Optional[str] = None, db: Session = Depends(get_db)):
    """Remove one dispatch event (e.g. a mistaken 'Dispatch More' entry) so it
    stops showing up in the WO fulfillment summary's dispatch history. This
    only removes the dispatch log entry — it does not touch the sale's
    invoiced WorkOrderSaleItem rows/quantities, which remain the source of
    truth for Completed/Pending totals. To reduce invoiced quantity, edit the
    sale's items instead."""
    sale = db.get(WorkOrderSale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Work order sale not found.")

    dispatch = db.get(WorkOrderSaleDispatch, dispatch_id)
    if not dispatch or dispatch.sale_id != sale_id:
        raise HTTPException(status_code=404, detail="Dispatch event not found.")

    dispatch_label = dispatch.invoice_number or sale.invoice_number
    db.delete(dispatch)
    db.commit()
    log_activity(
        db, "WO Sale Dispatch Deleted", "WorkOrderSale",
        f"Removed dispatch event ({dispatch_label}) from sale invoice {sale.invoice_number}.",
        deleted_by or "System", sale_id, entity_name=sale.invoice_number,
    )


@router.post("/{sale_id}/dispatches", response_model=WorkOrderSaleDispatchOut, status_code=status.HTTP_201_CREATED)
def add_work_order_sale_dispatch(sale_id: int, payload: WorkOrderSaleDispatchCreate, db: Session = Depends(get_db)):
    """Record one additional dispatch event ('Dispatch More') against an
    existing invoice, so the WO fulfillment summary can list it separately
    from the invoice's original dispatch — with its own date/qty/amount."""
    sale = db.get(WorkOrderSale, sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Work order sale not found.")

    data = payload.model_dump()
    items_data = data.pop("items", [])

    dispatch = WorkOrderSaleDispatch(sale_id=sale_id, **data)
    db.add(dispatch)
    db.flush()

    for item_data in items_data:
        db.add(WorkOrderSaleDispatchItem(dispatch_id=dispatch.id, **item_data))

    db.commit()
    db.refresh(dispatch)
    return dispatch
