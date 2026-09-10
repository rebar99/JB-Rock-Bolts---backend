from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Query, Header
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional, Dict, Any
from datetime import datetime
from app.database import get_db
from app.models.models import User, WorkOrder, WOLineItem
from app.schemas.work_order import WorkOrderCreate, WorkOrderUpdate, WorkOrderOut, WorkOrderClose
from app.schemas.bulk import BulkDeleteRequest, BulkDeleteResult
from app.utils.helpers import log_activity, generate_wo_number, values_equal_for_update
from app.utils.auth import get_current_user, require_admin, get_user_id_from_token
from app.routers.upload_helpers import (
    read_upload_bytes, save_upload_bytes, parse_import_file,
    parse_optional_datetime, make_excel_response, style_header_row,
)

router = APIRouter(prefix="/api/work-orders", tags=["Work Orders"])

CLOSED_STATUSES = ("Closed", "Cancelled")


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
async def upload_wo_file(file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    content = await read_upload_bytes(file)
    file_url = save_upload_bytes(content, file.filename)
    return {"file_url": file_url}


# ── Auto WO number ───────────────────────────────────────────────────────────

@router.get("/next-number")
def next_wo_number(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return {"wo_number": generate_wo_number(db)}


# ── Excel export ──────────────────────────────────────────────────────────────

@router.get("/export")
def export_work_orders(db: Session = Depends(get_db)):
    """Export all Work Orders to an Excel (.xlsx) file."""
    from openpyxl import Workbook

    orders = db.query(WorkOrder).order_by(WorkOrder.created_at.desc()).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Work Orders"

    headers = [
        "WO Number", "WO Date", "Client Name", "Project",
        "Item Name", "Quantity", "Completed Qty", "Pending Qty", "UOM", "Unit Price",
        "GST", "Freight", "Subtotal", "Row Total",
        "Status", "Priority", "Work Description", "Site Location",
        "Engineer/Supervisor", "Start Date", "Target Completion Date",
        "Created By", "Remark",
    ]
    ws.append(headers)
    style_header_row(ws, len(headers))

    for o in orders:
        items = o.line_items if o.line_items else [None]
        for li in items:
            if li:
                qty = float(li.quantity or 0)
                completed = float(li.completed_quantity or 0)
                pending = round(max(0, qty - completed), 10)
                price = float(li.unit_price or 0)
                sub = qty * price
                gst_str = str(li.gst or "0")
                gst_pct = _float(gst_str.replace("₹", "").replace("%", ""))
                gst_amt = _float(gst_str.replace("%", "")) if gst_str.startswith("₹") else sub * gst_pct / 100
                freight = float(li.freight or 0)
                row_total = sub + gst_amt + freight
                item_name = li.item
                uom = li.uom or "Nos"
            else:
                qty = float(o.total_quantity or 0)
                completed = 0.0
                pending = qty
                price = float(o.unit_price or 0)
                sub = qty * price
                gst_str = str(o.gst or "0")
                gst_pct = _float(gst_str.replace("₹", "").replace("%", ""))
                gst_amt = _float(gst_str.replace("%", "")) if gst_str.startswith("₹") else sub * gst_pct / 100
                freight = float(o.freight or 0)
                row_total = sub + gst_amt + freight
                item_name = o.item or ""
                uom = o.uom or "Nos"

            ws.append([
                o.wo_number, o.wo_date.strftime("%Y-%m-%d") if o.wo_date else "", o.client_name, o.project or "",
                item_name, qty, completed, pending, uom, price,
                gst_str, freight, round(sub, 2), round(row_total, 2),
                o.status or "Pending", o.priority or "Medium", o.work_description or "", o.site_location or "",
                o.engineer_name or "",
                o.start_date.strftime("%Y-%m-%d") if o.start_date else "",
                o.target_completion_date.strftime("%Y-%m-%d") if o.target_completion_date else "",
                o.created_by or "",
                o.remarks or "",
            ])

    return make_excel_response(wb, "work-orders-export.xlsx")


# ── Excel import ──────────────────────────────────────────────────────────────

@router.post("/import")
async def import_work_orders(
    file: UploadFile = File(...),
    on_conflict: str = Query("skip", description="skip | update"),
    created_by: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Import Work Orders from an Excel (.xlsx) or CSV file.

    on_conflict:
      skip   – leave existing WOs untouched (default)
      update – update header fields of existing WOs (line items preserved)
    """
    raw_rows = parse_import_file(await read_upload_bytes(file), file.filename)
    grouped: Dict[str, Dict[str, Any]] = {}

    for row in raw_rows:
        wo_number = _field(row, ["wo_number", "WO Number", "wo", "wo_no"])
        if not wo_number:
            continue
        key = str(wo_number).strip()
        if key not in grouped:
            grouped[key] = {"base": row, "rows": []}
        grouped[key]["rows"].append(row)

    created = updated = skipped = 0
    errors = []

    for wo_number, group in grouped.items():
        base = group["base"]
        rows = group["rows"]

        client_name = _field(base, ["client_name", "Client Name", "client", "customer"])
        if not client_name:
            errors.append(f"WO {wo_number}: missing client name – skipped")
            continue

        line_items_raw = []
        for row in rows:
            item_name = _field(row, ["item", "Item Name", "product", "description"])
            if not item_name:
                continue
            line_items_raw.append({
                "item": item_name,
                "quantity": _float(_field(row, ["quantity", "Quantity", "qty", "total_quantity"])),
                "completed_quantity": _float(_field(row, ["completed_quantity", "Completed Qty", "completed"])),
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
                    "completed_quantity": _float(_field(base, ["completed_quantity", "Completed Qty", "completed"])),
                    "uom": _field(base, ["uom", "UOM", "unit"]) or "Nos",
                    "unit_price": _float(_field(base, ["unit_price", "Unit Price", "price", "rate"])),
                    "gst": str(_field(base, ["gst", "GST", "gst_rate"]) or "0"),
                    "freight": _float(_field(base, ["freight", "Freight", "line_freight"])),
                }]

        existing = db.query(WorkOrder).filter(WorkOrder.wo_number == wo_number).first()

        if existing:
            if on_conflict == "skip":
                skipped += 1
                continue
            elif on_conflict == "update":
                existing.client_name = str(client_name)
                existing.project = str(_field(base, ["project", "Project", "project_name"]) or existing.project or "")
                existing.status = str(_field(base, ["status", "Status"]) or existing.status or "Pending")
                existing.priority = str(_field(base, ["priority", "Priority"]) or existing.priority or "Medium")
                existing.work_description = str(_field(base, ["work_description", "Work Description"]) or existing.work_description or "") or None
                existing.site_location = str(_field(base, ["site_location", "Site Location"]) or existing.site_location or "") or None
                existing.engineer_name = str(_field(base, ["engineer_name", "Engineer/Supervisor", "Engineer"]) or existing.engineer_name or "") or None
                wo_date_val = parse_optional_datetime(_field(base, ["wo_date", "WO Date"]))
                if wo_date_val:
                    existing.wo_date = wo_date_val
                target_val = parse_optional_datetime(_field(base, ["target_completion_date", "Target Completion Date"]))
                if target_val:
                    existing.target_completion_date = target_val
                start_val = parse_optional_datetime(_field(base, ["start_date", "Start Date"]))
                if start_val:
                    existing.start_date = start_val
                gst_val = _field(base, ["gst", "GST", "gst_rate"])
                if gst_val:
                    existing.gst = str(gst_val)
                freight_val = _field(base, ["freight", "Freight", "shipping_charge"])
                if freight_val not in (None, ""):
                    existing.freight = _float(freight_val)
                remark_val = _field(base, ["remark", "Remark", "remarks", "Remarks"])
                if remark_val not in (None, ""):
                    existing.remarks = str(remark_val)
                existing.last_updated_at = datetime.utcnow()
                existing.last_updated_by = created_by or "Import"
                try:
                    db.commit()
                    updated += 1
                except Exception as e:
                    db.rollback()
                    errors.append(f"WO {wo_number}: update failed – {str(e)}")
                continue

        wo_payload = {
            "client_name": str(client_name),
            "wo_number": wo_number,
            "project": str(_field(base, ["project", "Project", "project_name"]) or ""),
            "status": str(_field(base, ["status", "Status"]) or "Pending"),
            "priority": str(_field(base, ["priority", "Priority"]) or "Medium"),
            "gst": str(_field(base, ["gst", "GST", "gst_rate"]) or "0"),
            "freight": _float(_field(base, ["freight", "Freight", "shipping_charge", "freight_amount"])),
            "wo_date": parse_optional_datetime(_field(base, ["wo_date", "WO Date"])),
            "start_date": parse_optional_datetime(_field(base, ["start_date", "Start Date"])),
            "target_completion_date": parse_optional_datetime(_field(base, ["target_completion_date", "Target Completion Date"])),
            "work_description": str(_field(base, ["work_description", "Work Description"]) or "") or None,
            "site_location": str(_field(base, ["site_location", "Site Location"]) or "") or None,
            "engineer_name": str(_field(base, ["engineer_name", "Engineer/Supervisor", "Engineer"]) or "") or None,
            "remarks": str(_field(base, ["remark", "Remark", "remarks", "Remarks"]) or "") or None,
            "created_by": created_by or "Import",
            "line_items": line_items_raw,
        }

        try:
            payload = WorkOrderCreate(**wo_payload)
            create_work_order(payload, db=db)
            created += 1
        except HTTPException as e:
            errors.append(f"WO {wo_number}: {e.detail}")
        except Exception as e:
            errors.append(f"WO {wo_number}: {str(e)}")

    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors}


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[WorkOrderOut])
def list_work_orders(
    search: Optional[str] = None,
    client: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    db.expire_all()
    q = db.query(WorkOrder)
    if search:
        q = q.filter(
            (WorkOrder.wo_number.ilike(f"%{search}%")) |
            (WorkOrder.client_name.ilike(f"%{search}%")) |
            (WorkOrder.project.ilike(f"%{search}%")) |
            (WorkOrder.item.ilike(f"%{search}%"))
        )
    if client:
        q = q.filter(WorkOrder.client_name.ilike(f"%{client}%"))
    if status_filter:
        q = q.filter(WorkOrder.status == status_filter)
    return q.order_by(WorkOrder.created_at.desc()).offset(skip).limit(limit).all()


@router.post("", response_model=WorkOrderOut, status_code=status.HTTP_201_CREATED)
def create_work_order(payload: WorkOrderCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"line_items"})

    if payload.line_items:
        first = payload.line_items[0]
        data["item"] = first.item
        data["uom"] = first.uom
        data["unit_price"] = first.unit_price
        data["total_quantity"] = sum(li.quantity for li in payload.line_items)

    wo = WorkOrder(**data)

    for li_data in payload.line_items:
        wo.line_items.append(WOLineItem(**li_data.model_dump()))

    db.add(wo)
    try:
        db.commit()
        db.refresh(wo)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Work order with WO# '{payload.wo_number}' already exists.",
        )

    log_activity(db, "WO Created", "WorkOrder", f"Created WO {wo.wo_number} for {wo.client_name}.", payload.created_by or "System", wo.id, entity_name=wo.wo_number)
    return wo


@router.get("/{wo_id}", response_model=WorkOrderOut)
def get_work_order(wo_id: int, opened_by: Optional[str] = None, db: Session = Depends(get_db)):
    wo = db.get(WorkOrder, wo_id)
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found.")
    if opened_by:
        wo.last_opened_at = datetime.utcnow()
        wo.last_opened_by = opened_by
        db.commit()
        db.refresh(wo)
    return wo


@router.put("/{wo_id}", response_model=WorkOrderOut)
def update_work_order(wo_id: int, payload: WorkOrderUpdate, authorization: str = Header(default=None), db: Session = Depends(get_db)):
    wo = db.get(WorkOrder, wo_id)
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found.")

    if wo.status in CLOSED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot update a {wo.status} Work Order."
        )

    # last_updated_by is metadata (who to attribute a real change to), not a
    # comparable business field — it's excluded here so that simply having a
    # different user open the form doesn't itself register as a "change".
    update_data = payload.model_dump(exclude_unset=True, exclude={"line_items", "last_updated_by"})
    changed_fields = []
    for field, value in update_data.items():
        old_val = getattr(wo, field, None)
        if not values_equal_for_update(old_val, value):
            changed_fields.append(field)
            setattr(wo, field, value)

    # Only treat line items as changed if their actual values differ — the
    # edit form always resubmits the full line_items array even when the
    # user touched nothing, so a naive "payload.line_items is not None"
    # check would flag every save as a change.
    if payload.line_items is not None:
        existing_signature = [
            (li.item, float(li.quantity or 0), li.uom, float(li.unit_price or 0), str(li.gst or ""), float(li.freight or 0))
            for li in wo.line_items
        ]
        new_signature = [
            (li_data.item, float(li_data.quantity or 0), li_data.uom, float(li_data.unit_price or 0), str(li_data.gst or ""), float(li_data.freight or 0))
            for li_data in payload.line_items
        ]
        
        quantity_changed = False
        var_name = getattr(wo, 'po_number', getattr(wo, 'wo_number', 'unknown'))
        existing_items_dict = {li.id: li for li in wo.line_items if getattr(li, 'id', None)}
        for li_data in payload.line_items:
            if li_data.id and li_data.id in existing_items_dict:
                if float(li_data.quantity or 0) != float(existing_items_dict[li_data.id].quantity):
                    quantity_changed = True
                    break
            else:
                quantity_changed = True
                break
                
        if len(payload.line_items) != len(existing_items_dict):
            quantity_changed = True
            
        if quantity_changed:
            user_id = get_user_id_from_token(authorization) if authorization else None
            user = db.get(User, user_id) if user_id else None
            if not user or not user.is_admin:
                raise HTTPException(
                    status_code=403,
                    detail="Only Admin can update PO quantity." if not True else "Only Admin can update Work Order quantity."
                )

        if existing_signature != new_signature:
            changed_fields.append("line_items")
            existing_items = {li.id: li for li in wo.line_items if getattr(li, 'id', None)}

            new_line_items = []
            for li_data in payload.line_items:
                if li_data.id and li_data.id in existing_items:
                    li = existing_items[li_data.id]
                    # completed_quantity is intentionally NOT set from the client
                    # payload here — it is derived exclusively from Work Order
                    # Sale dispatches via recalc_wo_completed_quantities, the
                    # same discipline as PurchaseOrder.delivered_quantity.
                    if li_data.quantity < li.completed_quantity:
                        db.rollback()
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Cannot reduce quantity below the dispatched amount ({li.completed_quantity})."
                        )
                    
                    li.item = li_data.item
                    li.quantity = li_data.quantity
                    li.uom = li_data.uom
                    li.unit_price = li_data.unit_price
                    li.gst = li_data.gst
                    li.freight = li_data.freight
                    new_line_items.append(li)
                    del existing_items[li_data.id]
                else:
                    # New line items always start at 0 completed — same as
                    # POLineItem, which never accepts a client-supplied
                    # delivered_quantity on create either.
                    new_li = WOLineItem(
                        item=li_data.item,
                        quantity=li_data.quantity,
                        uom=li_data.uom,
                        unit_price=li_data.unit_price,
                        gst=li_data.gst,
                        freight=li_data.freight,
                    )
                    new_line_items.append(new_li)

            wo.line_items = new_line_items

            if wo.line_items:
                first = wo.line_items[0]
                wo.item = first.item
                wo.uom = first.uom
                wo.unit_price = first.unit_price
                wo.total_quantity = sum(li.quantity for li in wo.line_items)

    # Nothing to save — leave last_updated_at/by (and therefore the Activity
    # column) untouched, and skip logging a no-op "WO Updated" entry, so
    # clicking Save Changes without editing anything has no visible effect.
    if not changed_fields:
        db.rollback()
        result = WorkOrderOut.model_validate(wo).model_dump(mode="json")
        result["no_changes"] = True
        return result

    wo.last_updated_by = payload.last_updated_by
    wo.last_updated_at = datetime.utcnow()

    try:
        db.commit()
        db.refresh(wo)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Work order with WO# '{payload.wo_number}' already exists.",
        )

    details_str = f"Updated WO {wo.wo_number}. Changed fields: {', '.join(changed_fields)}"
    log_activity(
        db, "WO Updated", "WorkOrder", details_str,
        wo.last_updated_by or "System", wo.id,
        entity_name=wo.wo_number,
        changed_fields=", ".join(changed_fields),
    )
    result = WorkOrderOut.model_validate(wo).model_dump(mode="json")
    result["no_changes"] = False
    return result


@router.post("/{wo_id}/close", response_model=WorkOrderOut)
def close_work_order(wo_id: int, payload: WorkOrderClose, db: Session = Depends(get_db)):
    wo = db.get(WorkOrder, wo_id)
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found.")

    if wo.status in CLOSED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Work Order is already {wo.status}."
        )

    wo.status = "Closed"
    wo.closed_at = datetime.utcnow()
    wo.closed_by = payload.user
    wo.closed_remark = payload.remark

    db.commit()
    db.refresh(wo)

    details = f"Closed WO {wo.wo_number}."
    if payload.remark:
        details += f" Reason: {payload.remark}"

    log_activity(db, "WO Closed", "WorkOrder", details, payload.user or "System", wo.id, entity_name=wo.wo_number)
    return wo


@router.delete("/{wo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_work_order(wo_id: int, deleted_by: Optional[str] = None, db: Session = Depends(get_db)):
    wo = db.get(WorkOrder, wo_id)
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found.")

    wo_number = wo.wo_number
    # Deleting a WO cascades to any linked WO Sales/Invoices — each is
    # deleted (and logged) individually first, mirroring the PO delete flow.
    for sale in list(wo.work_order_sales):
        sale_id, invoice_number, client_name = sale.id, sale.invoice_number, sale.client_name
        db.delete(sale)
        db.flush()
        log_activity(
            db, "WO Sale Deleted", "WorkOrderSale",
            f"Deleted sale invoice {invoice_number} for {client_name} (cascaded from WO {wo_number} deletion).",
            deleted_by or "System", sale_id, entity_name=invoice_number,
        )

    db.delete(wo)
    db.commit()
    log_activity(db, "WO Deleted", "WorkOrder", f"Deleted WO {wo_number}.", deleted_by or "System", wo_id, entity_name=wo_number)


@router.post("/bulk-delete", response_model=BulkDeleteResult)
def bulk_delete_work_orders(payload: BulkDeleteRequest, db: Session = Depends(get_db)):
    """Delete many Work Orders in one request — best-effort per id. Mirrors
    delete_work_order's cascade: linked WO Sales are deleted (and logged)
    before the WO itself.
    """
    deleted: list[int] = []
    errors: list[str] = []
    for wo_id in payload.ids:
        wo = db.get(WorkOrder, wo_id)
        if not wo:
            errors.append(f"WO {wo_id}: not found")
            continue
        wo_number = wo.wo_number
        try:
            for sale in list(wo.work_order_sales):
                sale_id, invoice_number, client_name = sale.id, sale.invoice_number, sale.client_name
                db.delete(sale)
                db.flush()
                log_activity(
                    db, "WO Sale Deleted", "WorkOrderSale",
                    f"Deleted sale invoice {invoice_number} for {client_name} (cascaded from WO {wo_number} bulk deletion).",
                    payload.deleted_by or "System", sale_id, entity_name=invoice_number,
                )
            db.delete(wo)
            db.commit()
        except IntegrityError:
            db.rollback()
            errors.append(f"WO {wo_number}: database constraint prevented deletion")
            continue
        log_activity(db, "WO Deleted", "WorkOrder", f"Deleted WO {wo_number}.", payload.deleted_by or "System", wo_id, entity_name=wo_number)
        deleted.append(wo_id)
    return BulkDeleteResult(deleted=deleted, errors=errors)

from pydantic import BaseModel

class QuantityIncreaseItem_wo(BaseModel):
    line_item_id: int
    additional_quantity: float
    reason: str

class QuantityIncreaseRequest_wo(BaseModel):
    items: list[QuantityIncreaseItem_wo]

@router.post("/{wo_id}/increase-quantity", response_model=WorkOrderOut)
def increase_wo_quantity(
    wo_id: int,
    payload: QuantityIncreaseRequest_wo,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db)
):
    admin = require_admin(authorization, db, "Only Admin can update Work Order quantity.")
    wo = db.get(WorkOrder, wo_id)
    if not wo:
        raise HTTPException(status_code=404, detail="Work Order not found.")
        
    if getattr(wo, "short_closed", False) or getattr(wo, "status", "") in ["Closed", "Cancelled", "Completed"]:
        raise HTTPException(status_code=400, detail="Cannot update quantity for closed/completed Work Order.")

    item_map = {li.id: li for li in wo.line_items if li.id}
    
    logs = []
    total_added = 0
    for update_item in payload.items:
        li = item_map.get(update_item.line_item_id)
        if not li:
            raise HTTPException(status_code=400, detail=f"Line item {update_item.line_item_id} not found.")
            
        old_qty = li.quantity
        added_qty = update_item.additional_quantity
        new_qty = old_qty + added_qty
        
        if new_qty < getattr(li, "completed_quantity", 0):
            raise HTTPException(status_code=400, detail=f"Cannot reduce below dispatched amount for item {li.item}.")
            
        li.quantity = new_qty
        total_added += added_qty
        logs.append(f"Item '{li.item}': {old_qty} -> {new_qty} (+{added_qty})")
        
    db.commit()
    db.refresh(wo)
    
    identifier = getattr(wo, "po_number", getattr(wo, "wo_number", str(wo_id)))
    
    log_activity(
        db,
        action="Increase Quantity",
        entity_type="WorkOrder",
        details=f"Increased Work Order quantity. Reason: {payload.items[0].reason}. Changes: {'; '.join(logs)}",
        user=admin.name,
        entity_id=wo_id,
        entity_name=identifier
    )
    
    return wo
