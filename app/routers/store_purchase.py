import json
from datetime import date, datetime
import re
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.models.models import StoreVendor, StorePurchaseItem, StorePurchaseStockItem, StorePurchaseOrder, StorePurchaseOrderActivity, StorePurchaseOrderDetail, StorePurchaseOrderLine, StorePurchaseMaterialReceipt, StorePurchaseMaterialReceiptLine, StorePurchaseStockTransaction, StorePurchaseStockTransfer, StorePOItemMasterItem, User, POApprovalLevel, POApprovalLevelApprover, StorePurchaseOrderApproval
from app.utils.auth import get_current_user, require_application_access, require_super_admin
from app.utils.helpers import log_activity
from app.routers.upload_helpers import read_upload_bytes, save_upload_bytes

router = APIRouter(prefix="/api/store-purchase", tags=["Store Purchase"])

class ItemPayload(BaseModel):
    name: str
    quantity: float = 0
    reorder_level: float = 0
    vendor_name: str | None = None
    reference_no: str | None = None
    receipt_date: date | None = None
    stock_item: str | None = None
    uom: str = "Nos"
    location: str | None = None
    required_for: str | None = None
    rate: float = Field(default=0, ge=0)
    previous_quantity: float = Field(default=0, ge=0)
    current_month_quantity: float = Field(default=0, ge=0)

class IssuePayload(BaseModel):
    quantity: float = Field(gt=0)
    required_for: str | None = None
    issued_to: str | None = None
    location: str | None = None
    issue_date: date | None = None
    remarks: str | None = None

class TransferPayload(BaseModel):
    quantity: float = Field(gt=0)
    from_location: str = Field(min_length=1, max_length=150)
    to_location: str = Field(min_length=1, max_length=150)
    transfer_date: date | None = None
    required_for: str | None = None
    remarks: str | None = None

def _status(quantity: float, reorder_level: float) -> str:
    if quantity <= 0: return "Out of Stock"
    if quantity <= reorder_level: return "Low Stock"
    return "In Stock"


def _inventory_group_and_item(db: Session, description: str) -> tuple[str, str]:
    """Resolve a PO's combined item-master value into inventory group + item.

    The Store PO picker saves values as ``Group Name Item Name``. Inventory
    keeps the group as its master and the trailing value as the item/variant.
    """
    raw = description.strip()
    candidates = sorted(db.query(StorePOItemMasterItem).all(), key=lambda item: len(item.name), reverse=True)
    raw_lower = raw.lower()
    for master in candidates:
        group = master.name.strip()
        if raw_lower == group.lower():
            return group, "General"
        prefix = f"{group} ".lower()
        if raw_lower.startswith(prefix):
            return group, raw[len(group):].strip() or "General"
    # When no Store item-master mapping exists, PO descriptions commonly end
    # with a size (for example ``Round Black 42mm``). Keep that size as the
    # inventory Item Name and everything before it as the Group Name.
    size_match = re.match(r"^(.*?)(\d+(?:\.\d+)?\s*(?:mm|cm|m|inch|in|kg|g|mt|nos))$", raw, re.IGNORECASE)
    if size_match:
        return size_match.group(1).strip(" ,-"), size_match.group(2).strip()
    return raw, "General"

def _same_inventory_item(db: Session, group_name: str, stock_item: str | None):
    """Find one inventory row by its Group Name + Items Name pair."""
    item_name = (stock_item or "General").strip() or "General"
    return db.query(StorePurchaseItem).filter(
        func.lower(StorePurchaseItem.name) == group_name.strip().lower(),
        func.lower(func.coalesce(StorePurchaseItem.stock_item, "General")) == item_name.lower(),
    ).first()


def _item_response(item: StorePurchaseItem):
    # These fields are an explicit inventory allocation. A manual opening
    # balance (Previous Qty) plus this month's entry must remain visible as
    # entered; the ledger itself keeps the full movement audit separately.
    previous_quantity = item.previous_quantity or 0
    current_month_quantity = item.current_month_quantity or 0
    return {
        "id": item.id, "name": item.name, "quantity": item.quantity,
        "available_quantity": item.quantity, "total_stock": item.total_received_quantity or item.quantity,
        "total_quantity": item.total_received_quantity or item.quantity,
        "previous_quantity": previous_quantity, "current_month_quantity": current_month_quantity,
        "issued_quantity": item.issued_quantity,
        "remaining_quantity": item.quantity, "remaining_stock_amount": round((item.quantity or 0) * (item.rate or 0), 2),
        "reorder_level": item.reorder_level, "vendor_name": item.vendor_name, "reference_no": item.reference_no,
        "receipt_date": item.receipt_date, "stock_item": item.stock_item, "uom": item.uom,
        "location": item.location, "required_for": item.required_for, "rate": item.rate,
        "status": _status(item.quantity or 0, item.reorder_level or 0), "created_at": item.created_at,
        "stock_items": [{"id": stock.id, "name": stock.name, "uom": stock.uom, "location": stock.location,
                         "vendor_name": stock.vendor_name, "rate": stock.rate, "total_received_quantity": stock.total_received_quantity,
                         "issued_quantity": stock.issued_quantity, "available_quantity": stock.available_quantity,
                         "amount": round((stock.available_quantity or 0) * (stock.rate or 0), 2)} for stock in item.stock_items],
    }

def _record_receipt(db: Session, *, name: str, quantity: float, rate: float, vendor_name=None, reference_no=None,
                    receipt_date=None, uom="Nos", location=None, stock_item=None, required_for=None, user_name=None,
                    existing_item=None, existing_stock_item=None, receipt_line_id=None):
    normalized = name.strip()
    # This is deliberately enforced here (not only in the receipt endpoint),
    # so every inventory entry point uses exactly the same Group + Item split.
    if stock_item and stock_item.strip():
        normalized_stock_item = stock_item.strip()
    else:
        normalized, normalized_stock_item = _inventory_group_and_item(db, normalized)
    item = existing_item or _same_inventory_item(db, normalized, normalized_stock_item)
    receipt_date = receipt_date or date.today()
    is_current_month = (receipt_date.year, receipt_date.month) == (date.today().year, date.today().month)
    if item:
        # Previous Qty is the opening stock before the first receipt in the
        # current month. Current Month Qty is the sum of this month's receipts.
        if is_current_month:
            if not item.current_month_quantity:
                item.previous_quantity = item.quantity
            item.current_month_quantity = (item.current_month_quantity or 0) + quantity
        else:
            item.previous_quantity = (item.previous_quantity or 0) + quantity
        item.quantity += quantity
        item.total_received_quantity = (item.total_received_quantity or item.previous_quantity or 0) + quantity
        item.rate = rate or item.rate
        item.vendor_name = vendor_name or item.vendor_name
        item.reference_no = reference_no or item.reference_no
        item.receipt_date = receipt_date
        item.uom = uom or item.uom
        item.location = location or item.location
        item.stock_item = normalized_stock_item
        item.required_for = required_for or item.required_for
    else:
        item = StorePurchaseItem(name=normalized, quantity=quantity, total_received_quantity=quantity,
            previous_quantity=0 if is_current_month else quantity,
            current_month_quantity=quantity if is_current_month else 0,
            rate=rate,
            vendor_name=vendor_name, reference_no=reference_no, receipt_date=receipt_date, uom=uom or "Nos",
            location=location, stock_item=normalized_stock_item, required_for=required_for)
        db.add(item); db.flush()
    # Each material can carry multiple named stock items. Receipts against a
    # matching name update that stock item; a new name creates another child.
    child_name = normalized_stock_item
    stock = existing_stock_item
    if stock is None:
        stock = db.query(StorePurchaseStockItem).filter(
            StorePurchaseStockItem.material_id == item.id,
            func.lower(StorePurchaseStockItem.name) == child_name.lower(),
        ).first()
    if stock:
        stock.total_received_quantity += quantity
        stock.available_quantity += quantity
        stock.rate = rate or stock.rate
        stock.uom = uom or stock.uom
        stock.location = location or stock.location
        stock.vendor_name = vendor_name or stock.vendor_name
    else:
        db.add(StorePurchaseStockItem(material_id=item.id, name=child_name, uom=uom or "Nos", location=location,
               vendor_name=vendor_name, rate=rate, total_received_quantity=quantity, available_quantity=quantity))
    item.status = _status(item.quantity, item.reorder_level)
    db.add(StorePurchaseStockTransaction(item_id=item.id, transaction_type="RECEIPT", quantity=quantity, rate=rate,
        amount=round(quantity * rate, 2), transaction_date=receipt_date, vendor_name=vendor_name,
        reference_no=reference_no, location=location, required_for=required_for, created_by=user_name,
        receipt_line_id=receipt_line_id))
    return item


def _refresh_po_received_status(db: Session, order: StorePurchaseOrder):
    """Close a PO only after every ordered item is fully received across all receipts."""
    received_rows = (
        db.query(StorePurchaseMaterialReceiptLine.item_description, func.sum(StorePurchaseMaterialReceiptLine.received_quantity))
        .join(StorePurchaseMaterialReceipt, StorePurchaseMaterialReceiptLine.receipt_id == StorePurchaseMaterialReceipt.id)
        .filter(StorePurchaseMaterialReceipt.order_id == order.id)
        .group_by(StorePurchaseMaterialReceiptLine.item_description)
        .all()
    )
    received_by_item = {str(name).strip().lower(): float(quantity or 0) for name, quantity in received_rows}
    fully_received = bool(order.line_items) and all(
        received_by_item.get(line.item_description.strip().lower(), 0) >= float(line.quantity or 0)
        for line in order.line_items
    )
    if fully_received:
        order.status = "Completed"
    elif order.status == "Completed":
        order.status = "Partially Received"

class OrderLinePayload(BaseModel):
    item_description: str = Field(min_length=1, max_length=500)
    quantity: float = Field(gt=0)
    uom: str = Field(default="Nos", max_length=50)
    unit_rate: float = Field(default=0, ge=0)


class OrderPayload(BaseModel):
    order_number: str
    supplier: str
    po_type: str = "Regular Vendor PO"
    vendor_code: str | None = None
    vendor_address: str | None = None
    vendor_contact: str | None = None
    vendor_gst: str | None = None
    po_date: date | None = None
    reference: str | None = None
    delivery_address: str | None = None
    gst_rate: float = Field(default=18, ge=0, le=100)
    round_off: float = 0
    price_basis: str | None = None
    packing_terms: str | None = None
    freight_terms: str | None = None
    insurance_terms: str | None = None
    delivery_terms: str | None = None
    inspection_terms: str | None = None
    warranty_terms: str | None = None
    payment_terms: str | None = None
    quantity_variance: str | None = None
    notes: str | None = None
    items: list[OrderLinePayload] = Field(min_length=1)
    status: str = "Pending Approval"


class MaterialReceiptLinePayload(BaseModel):
    item_description: str = Field(min_length=1, max_length=500)
    ordered_quantity: float = Field(ge=0)
    received_quantity: float = Field(ge=0)
    uom: str = Field(default="Nos", max_length=50)
    unit_rate: float = Field(default=0, ge=0)


class MaterialReceiptPayload(BaseModel):
    order_id: int
    invoice_date: date
    invoice_number: str = Field(min_length=1, max_length=150)
    freight_charge: float = Field(default=0, ge=0)
    bill_file_url: str | None = None
    remarks: str | None = None
    lines: list[MaterialReceiptLinePayload] = Field(min_length=1)

class ApprovalLevelPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    min_amount: float = Field(ge=0)
    max_amount: float | None = Field(default=None, ge=0)
    approver_ids: list[int] = Field(min_length=1)
    approval_rule: str = "any"
    is_enabled: bool = True

class ApprovalActionPayload(BaseModel):
    action: str
    rejection_reason: str | None = None

def store_user(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return require_application_access(current_user, db, "store_purchase")

def store_admin(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return require_application_access(current_user, db, "store_purchase", admin=True)

def _level_response(level: POApprovalLevel):
    return {"id": level.id, "name": level.name, "min_amount": level.min_amount,
            "max_amount": level.max_amount, "approval_rule": level.approval_rule,
            "is_enabled": level.is_enabled, "approvers": [{"id": link.user.id, "name": link.user.name,
            "email": link.user.email} for link in level.approvers]}

def _active_levels_for_amount(db: Session, amount: float):
    # Multiple matching rows intentionally form sequential levels. Typical range
    # configurations do not overlap, giving one level; overlapping policies allow
    # an organisation to require progressive approvals without hard-coded limits.
    rows = db.query(POApprovalLevel).filter(POApprovalLevel.is_enabled == True,
        POApprovalLevel.min_amount <= amount,
        (POApprovalLevel.max_amount.is_(None)) | (POApprovalLevel.max_amount >= amount)).order_by(POApprovalLevel.min_amount, POApprovalLevel.id).all()
    # Older settings may have been entered one approver at a time. Treat same
    # range/name/rule rows as one logical approval level, never as stages.
    grouped = {}
    for row in rows:
        key = (row.name.strip().lower(), row.min_amount, row.max_amount, row.approval_rule)
        grouped.setdefault(key, []).append(row)
    return list(grouped.values())

def _set_current_approval_status(order: StorePurchaseOrder):
    grouped = {}
    for approval in order.approvals:
        grouped.setdefault(approval.level_order, []).append(approval)
    for _, rows in sorted(grouped.items()):
        if any(row.action == "Rejected" for row in rows):
            order.status = "Rejected"; return
        approved = any(row.action == "Approved" for row in rows) if rows[0].approval_rule == "any" else all(row.action == "Approved" for row in rows)
        if not approved:
            order.status = "Partially Approved" if any(r.action == "Approved" for earlier in grouped.values() for r in earlier) else "Pending Approval"
            return
    order.status = "Approved"

@router.get("/approval-settings")
def approval_settings(_: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    return {"levels": [_level_response(level) for level in db.query(POApprovalLevel).order_by(POApprovalLevel.min_amount, POApprovalLevel.id)],
            "users": [{"id": user.id, "name": user.name, "email": user.email} for user in db.query(User).filter(User.is_active == True).order_by(User.name)]}

@router.post("/approval-settings/levels")
def create_approval_level(payload: ApprovalLevelPayload, _: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    if payload.approval_rule not in {"any", "all"}: raise HTTPException(status_code=422, detail="Approval rule must be 'any' or 'all'.")
    if payload.max_amount is not None and payload.max_amount < payload.min_amount: raise HTTPException(status_code=422, detail="Maximum amount must be greater than or equal to minimum amount.")
    users = db.query(User).filter(User.id.in_(set(payload.approver_ids)), User.is_active == True).all()
    if len(users) != len(set(payload.approver_ids)): raise HTTPException(status_code=422, detail="Every selected approver must be an active user.")
    # Saving another approver for the same range/level updates that one level
    # instead of creating a visually and logically separate workflow stage.
    level = db.query(POApprovalLevel).filter_by(name=payload.name.strip(), min_amount=payload.min_amount,
        max_amount=payload.max_amount, approval_rule=payload.approval_rule).first()
    if not level:
        level = POApprovalLevel(name=payload.name.strip(), min_amount=payload.min_amount, max_amount=payload.max_amount, approval_rule=payload.approval_rule, is_enabled=payload.is_enabled)
        db.add(level); db.flush()
    level.is_enabled = payload.is_enabled
    existing_ids = {item.user_id for item in level.approvers}
    db.add_all([POApprovalLevelApprover(level_id=level.id, user_id=user.id) for user in users if user.id not in existing_ids])
    db.commit(); db.refresh(level); return _level_response(level)

@router.put("/approval-settings/levels/{level_id}")
def update_approval_level(level_id: int, payload: ApprovalLevelPayload, _: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    level = db.get(POApprovalLevel, level_id)
    if not level: raise HTTPException(status_code=404, detail="Approval level not found.")
    if payload.approval_rule not in {"any", "all"} or (payload.max_amount is not None and payload.max_amount < payload.min_amount): raise HTTPException(status_code=422, detail="Invalid approval rule or amount range.")
    users = db.query(User).filter(User.id.in_(set(payload.approver_ids)), User.is_active == True).all()
    if len(users) != len(set(payload.approver_ids)): raise HTTPException(status_code=422, detail="Every selected approver must be an active user.")
    level.name = payload.name.strip(); level.min_amount = payload.min_amount; level.max_amount = payload.max_amount; level.approval_rule = payload.approval_rule; level.is_enabled = payload.is_enabled
    db.query(POApprovalLevelApprover).filter_by(level_id=level.id).delete()
    db.add_all([POApprovalLevelApprover(level_id=level.id, user_id=user.id) for user in users])
    db.commit(); db.refresh(level); return _level_response(level)

@router.delete("/approval-settings/levels/{level_id}")
def delete_approval_level(level_id: int, _: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    level = db.get(POApprovalLevel, level_id)
    if not level: raise HTTPException(status_code=404, detail="Approval level not found.")
    db.delete(level); db.commit(); return {"message": "Approval level deleted."}

@router.delete("/approval-settings/levels/{level_id}/approvers/{user_id}")
def remove_level_approver(level_id: int, user_id: int, _: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Remove one approver while preserving the configured level and range."""
    level = db.get(POApprovalLevel, level_id)
    if not level: raise HTTPException(status_code=404, detail="Approval level not found.")
    link = db.query(POApprovalLevelApprover).filter_by(level_id=level_id, user_id=user_id).first()
    if not link: raise HTTPException(status_code=404, detail="Approver is not assigned to this approval level.")
    if len(level.approvers) <= 1:
        raise HTTPException(status_code=422, detail="An approval level must retain at least one approver. Delete the level if it is no longer needed.")
    db.delete(link); db.commit()
    return {"message": "Approver removed from approval level."}

@router.get("/purchase-orders/pending-approvals")
def pending_approvals(user: User = Depends(store_user), db: Session = Depends(get_db)):
    # Return only the current open stage. Pending rows kept from an "any one"
    # stage are historical assignment records, not actionable notifications.
    orders = db.query(StorePurchaseOrder).filter(StorePurchaseOrder.status.in_(["Pending Approval", "Partially Approved"])).all()
    result = []
    for order in orders:
        stages = {}
        for row in order.approvals: stages.setdefault(row.level_order, []).append(row)
        for _, rows in sorted(stages.items()):
            complete = any(x.action == "Approved" for x in rows) if rows[0].approval_rule == "any" else all(x.action == "Approved" for x in rows)
            if not complete:
                if any(x.approver_id == user.id and x.action == "Pending" for x in rows): result.append(_order_response(order))
                break
    return result

@router.post("/purchase-orders/{order_id}/approval")
def act_on_approval(order_id: int, payload: ApprovalActionPayload, user: User = Depends(store_user), db: Session = Depends(get_db)):
    if payload.action not in {"Approved", "Rejected"}: raise HTTPException(status_code=422, detail="Action must be Approved or Rejected.")
    if payload.action == "Rejected" and not (payload.rejection_reason or "").strip(): raise HTTPException(status_code=422, detail="A rejection reason is required.")
    order = db.get(StorePurchaseOrder, order_id)
    if not order or order.status not in {"Pending Approval", "Partially Approved"}: raise HTTPException(status_code=409, detail="This Purchase Order is not awaiting approval.")
    row = db.query(StorePurchaseOrderApproval).filter_by(order_id=order_id, approver_id=user.id, action="Pending").order_by(StorePurchaseOrderApproval.level_order).first()
    if not row: raise HTTPException(status_code=403, detail="You are not assigned as an approver for this Purchase Order.")
    # Later stages cannot be actioned before all earlier rules are satisfied.
    prior = [x for x in order.approvals if x.level_order < row.level_order]
    for stage in set(x.level_order for x in prior):
        group = [x for x in prior if x.level_order == stage]
        complete = any(x.action == "Approved" for x in group) if group[0].approval_rule == "any" else all(x.action == "Approved" for x in group)
        if not complete: raise HTTPException(status_code=409, detail="The previous approval level must be completed first.")
    row.action = payload.action; row.action_at = datetime.utcnow(); row.rejection_reason = (payload.rejection_reason or "").strip() or None
    _set_current_approval_status(order)
    db.add(StorePurchaseOrderActivity(order_id=order.id, action=f"Approval {payload.action}", details=f"{row.level_name}" + (f": {row.rejection_reason}" if row.rejection_reason else ""), performed_by=user.name))
    db.commit(); db.refresh(order)
    log_activity(db, f"Store PO {payload.action}", "StorePurchaseOrder", f"Store PO {order.order_number} {payload.action.lower()} by {user.name}.", user.name, entity_id=order.id, entity_name=order.order_number, workspace="Store")
    return _order_response(order)

@router.get("/dashboard")
def dashboard(_: User = Depends(store_user), db: Session = Depends(get_db)):
    items = db.query(StorePurchaseItem).all()
    return {"item_count": len(items), "low_stock_count": sum(1 for i in items if i.quantity <= i.reorder_level),
            "purchase_order_count": db.query(StorePurchaseOrder).count()}

@router.get("/inventory")
def inventory(_: User = Depends(store_user), db: Session = Depends(get_db)):
    return [_item_response(item) for item in db.query(StorePurchaseItem).order_by(StorePurchaseItem.name).all()]

@router.post("/inventory")
def create_item(payload: ItemPayload, _: User = Depends(store_admin), db: Session = Depends(get_db)):
    if not payload.name.strip(): raise HTTPException(status_code=422, detail="Material name is required.")
    group_name, item_name = (payload.name.strip(), payload.stock_item.strip()) if payload.stock_item and payload.stock_item.strip() else _inventory_group_and_item(db, payload.name)
    existing = _same_inventory_item(db, group_name, item_name)
    if existing and payload.quantity <= 0:
        raise HTTPException(status_code=409, detail="Material already exists. Add a receipt quantity to update its stock.")
    item = _record_receipt(db, name=group_name, quantity=payload.quantity, rate=payload.rate,
        vendor_name=payload.vendor_name, reference_no=payload.reference_no, receipt_date=payload.receipt_date,
        uom=payload.uom, location=payload.location, stock_item=item_name, required_for=payload.required_for,
        user_name=_.name)
    item.reorder_level = payload.reorder_level
    if not existing: item.previous_quantity = payload.previous_quantity
    item.current_month_quantity = payload.current_month_quantity
    db.commit(); db.refresh(item)
    log_activity(db, "Inventory Added", "StoreInventory", f"Added inventory item '{item.name}' (qty: {payload.quantity}).", _.name, entity_id=item.id, entity_name=item.name, workspace="Store")
    return _item_response(item)

@router.put("/inventory/{item_id}")
def update_item(item_id: int, payload: ItemPayload, _: User = Depends(store_admin), db: Session = Depends(get_db)):
    item = db.get(StorePurchaseItem, item_id)
    if not item: raise HTTPException(status_code=404, detail="Item not found.")
    duplicate = _same_inventory_item(db, payload.name, payload.stock_item)
    if duplicate and duplicate.id != item_id:
        raise HTTPException(status_code=409, detail="This Group Name and Items Name combination already exists.")
    
    for key, value in payload.model_dump(exclude={"quantity", "previous_quantity", "current_month_quantity"}).items(): setattr(item, key, value)
    
    item.previous_quantity = payload.previous_quantity
    item.current_month_quantity = payload.current_month_quantity
    item.total_received_quantity = payload.quantity
    item.quantity = payload.quantity - (item.issued_quantity or 0)
    
    item.status = _status(item.quantity, item.reorder_level)
    db.commit(); db.refresh(item)
    log_activity(db, "Inventory Updated", "StoreInventory", f"Updated inventory item '{item.name}'.", _.name, entity_id=item.id, entity_name=item.name, workspace="Store")
    return _item_response(item)

@router.post("/inventory/{item_id}/issue")
def issue_item(item_id: int, payload: IssuePayload, user: User = Depends(store_admin), db: Session = Depends(get_db)):
    item = db.get(StorePurchaseItem, item_id)
    if not item: raise HTTPException(status_code=404, detail="Material not found.")
    if payload.quantity > item.quantity:
        raise HTTPException(status_code=422, detail=f"Insufficient stock. Only {item.quantity:g} units are available.")
    issued_to = (payload.issued_to or "").strip()
    target_location = (payload.location or "").strip()
    if not issued_to and not target_location:
        raise HTTPException(status_code=422, detail="Department / Person or Location is required.")
    issue_date = payload.issue_date or date.today()
    item.quantity -= payload.quantity
    item.issued_quantity = (item.issued_quantity or 0) + payload.quantity
    # Vendor-wise issue quantities are read from the named stock-item rows.
    # Keep those rows in sync with the parent material whenever stock is
    # issued, consuming available stock in a stable FIFO order.
    remaining_to_issue = payload.quantity
    for stock in sorted(item.stock_items, key=lambda row: row.id):
        if remaining_to_issue <= 0:
            break
        available = max(0, stock.available_quantity or 0)
        issued_from_stock = min(available, remaining_to_issue)
        if issued_from_stock <= 0:
            continue
        stock.available_quantity = available - issued_from_stock
        stock.issued_quantity = (stock.issued_quantity or 0) + issued_from_stock
        remaining_to_issue -= issued_from_stock
    item.required_for = payload.required_for or item.required_for
    item.status = _status(item.quantity, item.reorder_level)
    final_issued_to = issued_to or target_location
    final_location = target_location or item.location
    tx = StorePurchaseStockTransaction(item_id=item.id, transaction_type="ISSUE", quantity=payload.quantity,
        rate=item.rate or 0, amount=round(payload.quantity * (item.rate or 0), 2), transaction_date=issue_date,
        required_for=payload.required_for, issued_to=final_issued_to, location=final_location, remarks=payload.remarks,
        created_by=user.name)
    db.add(tx); db.commit(); db.refresh(item)
    log_activity(db, "Stock Issued", "StoreInventory", f"Issued {payload.quantity} unit(s) of '{item.name}' to {final_issued_to}.", user.name, entity_id=item.id, entity_name=item.name, workspace="Store")
    return {"item": _item_response(item), "transaction": _transaction_response(tx)}

def _transaction_response(tx: StorePurchaseStockTransaction):
    return {"id": tx.id, "type": tx.transaction_type, "quantity": tx.quantity, "rate": tx.rate, "amount": tx.amount,
            "date": tx.transaction_date, "vendor_name": tx.vendor_name, "reference_no": tx.reference_no,
            "required_for": tx.required_for, "issued_to": tx.issued_to, "location": tx.location, "remarks": tx.remarks}

@router.get("/inventory/{item_id}/history")
def item_history(item_id: int, _: User = Depends(store_user), db: Session = Depends(get_db)):
    if not db.get(StorePurchaseItem, item_id): raise HTTPException(status_code=404, detail="Material not found.")
    return [_transaction_response(tx) for tx in db.query(StorePurchaseStockTransaction).filter_by(item_id=item_id).order_by(StorePurchaseStockTransaction.transaction_date.desc(), StorePurchaseStockTransaction.id.desc()).all()]

@router.get("/inventory/all-transactions")
def all_transactions(_: User = Depends(store_user), db: Session = Depends(get_db)):
    txs = (db.query(StorePurchaseStockTransaction, StorePurchaseItem.name)
           .join(StorePurchaseItem, StorePurchaseStockTransaction.item_id == StorePurchaseItem.id)
           .order_by(StorePurchaseStockTransaction.transaction_date.desc(), StorePurchaseStockTransaction.id.desc())
           .all())
    result = []
    for tx, item_name in txs:
        row = _transaction_response(tx)
        row["item_name"] = item_name
        row["item_id"] = tx.item_id
        result.append(row)
    return result


@router.post("/inventory/{item_id}/transfer")
def transfer_stock(item_id: int, payload: TransferPayload, user: User = Depends(store_admin), db: Session = Depends(get_db)):
    item = db.get(StorePurchaseItem, item_id)
    if not item: raise HTTPException(status_code=404, detail="Material not found.")
    if payload.from_location.strip().lower() == payload.to_location.strip().lower():
        raise HTTPException(status_code=422, detail="Source and destination store must be different.")
    if payload.quantity > item.quantity:
        raise HTTPException(status_code=422, detail=f"Insufficient stock. Only {item.quantity:g} units are available.")
    transfer_date = payload.transfer_date or date.today()
    transfer = StorePurchaseStockTransfer(item_id=item.id, quantity=payload.quantity,
        from_location=payload.from_location.strip(), to_location=payload.to_location.strip(),
        transfer_date=transfer_date, required_for=payload.required_for, remarks=payload.remarks, created_by=user.name)
    db.add(transfer)
    # Company total remains the same; the ledger documents the custody/location movement.
    db.add(StorePurchaseStockTransaction(item_id=item.id, transaction_type="TRANSFER", quantity=payload.quantity,
        rate=item.rate or 0, amount=round(payload.quantity * (item.rate or 0), 2), transaction_date=transfer_date,
        required_for=payload.required_for, location=f"{payload.from_location.strip()} → {payload.to_location.strip()}",
        remarks=payload.remarks, created_by=user.name))
    db.commit(); db.refresh(transfer)
    log_activity(db, "Stock Transferred", "StoreInventory", f"Transferred {payload.quantity} unit(s) of '{item.name}' from {payload.from_location.strip()} to {payload.to_location.strip()}.", user.name, entity_id=item.id, entity_name=item.name, workspace="Store")
    return {"id": transfer.id, "item_id": item.id, "quantity": transfer.quantity, "from_location": transfer.from_location,
            "to_location": transfer.to_location, "transfer_date": transfer.transfer_date, "message": "Stock transfer recorded."}

@router.delete("/inventory/{item_id}")
def delete_item(item_id: int, _: User = Depends(store_admin), db: Session = Depends(get_db)):
    item = db.get(StorePurchaseItem, item_id)
    if not item: raise HTTPException(status_code=404, detail="Item not found.")
    # Delete transfer records first (no cascade on this FK)
    db.query(StorePurchaseStockTransfer).filter(StorePurchaseStockTransfer.item_id == item_id).delete(synchronize_session=False)
    item_name = item.name
    db.delete(item); db.commit()
    log_activity(db, "Inventory Deleted", "StoreInventory", f"Deleted inventory item '{item_name}'.", _.name, entity_id=item_id, entity_name=item_name, workspace="Store")
    return {"message": "Item deleted."}

@router.get("/purchase-orders")
def orders(_: User = Depends(store_user), db: Session = Depends(get_db)):
    # "Draft" is no longer a Store PO status.  Keep legacy records usable
    # rather than exposing a retired status in the Purchase Order list.
    if db.query(StorePurchaseOrder).filter(StorePurchaseOrder.status == "Draft").update({"status": "Pending Approval"}, synchronize_session=False):
        db.commit()
    records = db.query(StorePurchaseOrder).order_by(StorePurchaseOrder.created_at.desc()).all()
    return [_order_response(record) for record in records]


@router.get("/purchase-orders/{order_id}")
def get_order(order_id: int, _: User = Depends(store_user), db: Session = Depends(get_db)):
    order = db.get(StorePurchaseOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Purchase order not found.")
    if order.status == "Draft":
        order.status = "Pending Approval"
        db.commit()
        db.refresh(order)
    return _order_response(order)


@router.post("/materials-received/upload")
async def upload_material_bill(file: UploadFile = File(...), _: User = Depends(store_admin)):
    """Attach an invoice/bill to a Store PO receipt without touching Inventory."""
    content = await read_upload_bytes(file)
    return {"file_url": save_upload_bytes(content, file.filename, "store-material-receipts"), "filename": file.filename}


class MaterialReceiptBillPatch(BaseModel):
    file_url: str | None = None
    updated_by: str | None = None


@router.patch("/materials-received/{receipt_id}/bill")
def patch_material_receipt_bill(receipt_id: int, payload: MaterialReceiptBillPatch, user: User = Depends(store_admin), db: Session = Depends(get_db)):
    """Update only the bill file URL on an existing material receipt."""
    receipt = db.get(StorePurchaseMaterialReceipt, receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Material receipt not found.")
    receipt.bill_file_url = payload.file_url
    receipt.updated_by = payload.updated_by or user.name
    receipt.updated_at = datetime.utcnow()
    db.commit(); db.refresh(receipt)
    return _material_receipt_response(receipt)


class MaterialReceiptPaymentPatch(BaseModel):
    payment_status: str
    amount_paid: float = Field(default=0, ge=0)
    payment_date: date | None = None
    payment_reference: str | None = None


@router.patch("/materials-received/{receipt_id}/payment")
def patch_material_receipt_payment(receipt_id: int, payload: MaterialReceiptPaymentPatch, user: User = Depends(store_admin), db: Session = Depends(get_db)):
    """Update payment status on an existing material receipt."""
    if payload.payment_status not in {"Unpaid", "Partially Paid", "Paid"}:
        raise HTTPException(status_code=422, detail="Payment status must be Unpaid, Partially Paid, or Paid.")
    receipt = db.get(StorePurchaseMaterialReceipt, receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Material receipt not found.")
    receipt.payment_status = payload.payment_status
    receipt.amount_paid = payload.amount_paid
    receipt.payment_date = payload.payment_date
    receipt.payment_reference = (payload.payment_reference or "").strip() or None
    receipt.updated_by = user.name
    receipt.updated_at = datetime.utcnow()
    db.commit(); db.refresh(receipt)
    log_activity(db, "Payment Status Updated", "StorePurchaseOrder", f"Updated payment status to '{payload.payment_status}' (amount: ₹{payload.amount_paid:,.2f}) for receipt #{receipt.id}.", user.name, entity_id=receipt.id, workspace="Store")
    return _material_receipt_response(receipt)


@router.get("/materials-received")
def material_receipts(_: User = Depends(store_user), db: Session = Depends(get_db)):
    records = db.query(StorePurchaseMaterialReceipt).order_by(StorePurchaseMaterialReceipt.created_at.desc()).all()
    return [_material_receipt_response(record) for record in records]


@router.post("/materials-received")
def create_material_receipt(payload: MaterialReceiptPayload, user: User = Depends(store_admin), db: Session = Depends(get_db)):
    order = db.get(StorePurchaseOrder, payload.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Purchase Order not found.")
    if order.status != "Approved":
        raise HTTPException(status_code=409, detail="Only a finally approved Purchase Order can proceed to Materials Received.")
    invoice_number = payload.invoice_number.strip()
    if not invoice_number:
        raise HTTPException(status_code=422, detail="Invoice number is required.")
    existing_invoice = db.query(StorePurchaseMaterialReceipt).filter(
        func.lower(func.trim(StorePurchaseMaterialReceipt.invoice_number)) == invoice_number.lower(),
    ).first()
    if existing_invoice:
        raise HTTPException(
            status_code=409,
            detail=f"Invoice number '{invoice_number}' is already used in Material Received ({existing_invoice.order.order_number}).",
        )
    ordered_by_item = {line.item_description.strip().lower(): line.quantity for line in order.line_items}
    already_received = {
        str(item).strip().lower(): float(quantity or 0)
        for item, quantity in db.query(
            StorePurchaseMaterialReceiptLine.item_description,
            func.sum(StorePurchaseMaterialReceiptLine.received_quantity),
        ).join(StorePurchaseMaterialReceipt).filter(
            StorePurchaseMaterialReceipt.order_id == order.id,
        ).group_by(StorePurchaseMaterialReceiptLine.item_description).all()
    }
    accepted_lines = [line for line in payload.lines if line.received_quantity > 0]
    if not accepted_lines:
        raise HTTPException(status_code=422, detail="Enter a received quantity greater than zero for at least one item.")
    for line in accepted_lines:
        ordered_quantity = ordered_by_item.get(line.item_description.strip().lower())
        if ordered_quantity is None:
            raise HTTPException(status_code=422, detail=f"'{line.item_description}' is not part of this Purchase Order.")
        remaining_quantity = max(0, ordered_quantity - already_received.get(line.item_description.strip().lower(), 0))
        if line.received_quantity > remaining_quantity:
            raise HTTPException(status_code=422, detail=f"Received quantity for '{line.item_description}' cannot exceed remaining PO quantity ({remaining_quantity:g}).")
    detail = order.details
    receipt = StorePurchaseMaterialReceipt(
        order_id=order.id, invoice_date=payload.invoice_date, invoice_number=invoice_number,
        freight_charge=payload.freight_charge, bill_file_url=payload.bill_file_url, vendor_name=order.supplier, reference=detail.reference if detail else None,
        vendor_code=detail.vendor_code if detail else None, remarks=payload.remarks, created_by=user.name,
    )
    db.add(receipt); db.flush()
    receipt_lines = [
        StorePurchaseMaterialReceiptLine(
            receipt_id=receipt.id, line_number=index, item_description=line.item_description.strip(),
            ordered_quantity=ordered_by_item[line.item_description.strip().lower()], received_quantity=line.received_quantity,
            uom=line.uom.strip() or "Nos", unit_rate=line.unit_rate,
            amount=round(line.received_quantity * line.unit_rate, 2),
        )
        for index, line in enumerate(accepted_lines, start=1)
    ]
    db.add_all(receipt_lines)
    db.flush()
    for line in receipt_lines:
        group_name, item_name = _inventory_group_and_item(db, line.item_description)
        _record_receipt(
            db, name=group_name, stock_item=item_name, quantity=line.received_quantity, rate=line.unit_rate,
            vendor_name=order.supplier, reference_no=order.order_number, receipt_date=payload.invoice_date,
            uom=line.uom, user_name=user.name, receipt_line_id=line.id,
        )
    _refresh_po_received_status(db, order)
    db.commit(); db.refresh(receipt)
    log_activity(db, "Materials Received", "StorePurchaseOrder", f"Materials received for PO {order.order_number} (Invoice: {invoice_number}).", user.name, entity_id=order.id, entity_name=order.order_number, workspace="Store")
    return _material_receipt_response(receipt)


@router.put("/materials-received/{receipt_id}")
def update_material_receipt(receipt_id: int, payload: MaterialReceiptPayload, user: User = Depends(store_admin), db: Session = Depends(get_db)):
    receipt = db.get(StorePurchaseMaterialReceipt, receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Material receipt not found.")
    raise HTTPException(status_code=409, detail="A posted material receipt cannot be edited because its inventory transaction is already recorded.")
    order = db.get(StorePurchaseOrder, payload.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Purchase Order not found.")
    ordered_by_item = {line.item_description.strip().lower(): line.quantity for line in order.line_items}
    for line in payload.lines:
        ordered_quantity = ordered_by_item.get(line.item_description.strip().lower())
        if ordered_quantity is None or line.received_quantity > ordered_quantity:
            raise HTTPException(status_code=422, detail=f"Invalid received quantity for '{line.item_description}'.")
    detail = order.details
    receipt.order_id = order.id; receipt.invoice_date = payload.invoice_date; receipt.invoice_number = payload.invoice_number.strip()
    receipt.freight_charge = payload.freight_charge; receipt.bill_file_url = payload.bill_file_url; receipt.vendor_name = order.supplier
    receipt.reference = detail.reference if detail else None; receipt.vendor_code = detail.vendor_code if detail else None; receipt.remarks = payload.remarks
    db.query(StorePurchaseMaterialReceiptLine).filter_by(receipt_id=receipt.id).delete(synchronize_session=False)
    db.add_all([StorePurchaseMaterialReceiptLine(receipt_id=receipt.id, line_number=index, item_description=line.item_description.strip(), ordered_quantity=line.ordered_quantity, received_quantity=line.received_quantity, uom=line.uom.strip() or "Nos", unit_rate=line.unit_rate, amount=round(line.received_quantity * line.unit_rate, 2)) for index, line in enumerate(payload.lines, start=1)])
    db.flush()
    _refresh_po_received_status(db, order)
    db.commit(); db.refresh(receipt)
    return _material_receipt_response(receipt)


@router.delete("/materials-received/{receipt_id}")
def delete_material_receipt(receipt_id: int, _: User = Depends(store_admin), db: Session = Depends(get_db)):
    receipt = db.get(StorePurchaseMaterialReceipt, receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Material receipt not found.")
    receipt_line_ids = [line.id for line in receipt.lines]
    has_inventory_posting = bool(receipt_line_ids) and db.query(StorePurchaseStockTransaction).filter(
        StorePurchaseStockTransaction.receipt_line_id.in_(receipt_line_ids)
    ).first()
    if has_inventory_posting:
        raise HTTPException(status_code=409, detail="Delete or reverse the linked inventory posting before deleting this material receipt.")
    order = receipt.order
    # A receipt is the final record for its Purchase Order in this workflow.
    # Deleting it deletes the linked PO and cascades to every related receipt,
    # PO line and PO detail. Inventory remains a separate manual workflow.
    db.delete(order)
    db.commit()
    return {"message": "Material receipt and linked Purchase Order deleted."}

@router.post("/purchase-orders")
def create_order(payload: OrderPayload, user: User = Depends(store_admin), db: Session = Depends(get_db)):
    if db.query(StorePurchaseOrder).filter_by(order_number=payload.order_number).first():
        raise HTTPException(status_code=409, detail="Order number already exists.")
    subtotal = round(sum(line.quantity * line.unit_rate for line in payload.items), 2)
    gst_amount = round(subtotal * payload.gst_rate / 100, 2)
    grand_total = round(subtotal + gst_amount + payload.round_off, 2)
    first_line = payload.items[0]
    applicable_levels = _active_levels_for_amount(db, grand_total)
    for level_group in applicable_levels:
        level = level_group[0]
        approvers = [link for configured_level in level_group for link in configured_level.approvers]
        if not approvers:
            raise HTTPException(status_code=422, detail=f"Approval level '{level.name}' has no approvers.")
    order = StorePurchaseOrder(
        order_number=payload.order_number.strip(), supplier=payload.supplier.strip(),
        item_name=first_line.item_description, quantity=sum(line.quantity for line in payload.items),
        unit_price=first_line.unit_rate, status="Pending Approval" if applicable_levels else "Approved", po_type=payload.po_type, created_by=user.name,
    )
    db.add(order); db.flush()
    detail_data = payload.model_dump(exclude={"order_number", "supplier", "po_type", "items", "status"})
    detail = StorePurchaseOrderDetail(
        order_id=order.id, **detail_data, subtotal=subtotal, gst_amount=gst_amount, grand_total=grand_total,
    )
    db.add(detail)
    db.add_all([
        StorePurchaseOrderLine(
            order_id=order.id, line_number=index, item_description=line.item_description.strip(),
            quantity=line.quantity, uom=line.uom.strip() or "Nos", unit_rate=line.unit_rate,
            amount=round(line.quantity * line.unit_rate, 2),
        )
        for index, line in enumerate(payload.items, start=1)
    ])
    for level_order, level_group in enumerate(applicable_levels, start=1):
        primary = level_group[0]
        approver_ids = {link.user_id for level in level_group for link in level.approvers}
        db.add_all([StorePurchaseOrderApproval(order_id=order.id, level_id=primary.id, level_name=primary.name,
            level_order=level_order, approval_rule=primary.approval_rule, approver_id=approver_id)
            for approver_id in approver_ids])
    db.add(StorePurchaseOrderActivity(
        order_id=order.id,
        action="Created",
        details="Purchase Order submitted for approval." if applicable_levels else "Purchase Order created; no approval level applies.",
        performed_by=user.name,
    ))
    # Purchase Orders and Items/Stock are intentionally separate workflows.
    # Saving a PO records only the vendor order; stock changes happen only via
    # Add/New Stock and Stock Operations, never by a PO create/update action.
    db.commit(); db.refresh(order)
    log_activity(db, "Store PO Created", "StorePurchaseOrder", f"Created Store PO {order.order_number} for {order.supplier}.", user.name, entity_id=order.id, entity_name=order.order_number, workspace="Store")
    return _order_response(order)

@router.delete("/purchase-orders/{order_id}")
def delete_order(order_id: int, _: User = Depends(store_admin), db: Session = Depends(get_db)):
    """Delete the PO without changing independent Items/Stock records."""
    order = db.get(StorePurchaseOrder, order_id)
    if not order: raise HTTPException(status_code=404, detail="Purchase Order not found.")
    order_number = order.order_number
    db.delete(order); db.commit()
    log_activity(db, "Store PO Deleted", "StorePurchaseOrder", f"Deleted Store PO {order_number}.", _.name, entity_name=order_number, workspace="Store")
    return {"message": "Purchase Order deleted."}

@router.put("/purchase-orders/{order_id}")
def update_order(order_id: int, payload: OrderPayload, user: User = Depends(store_admin), db: Session = Depends(get_db)):
    """Update Purchase Order independently from Items/Stock."""
    order = db.get(StorePurchaseOrder, order_id)
    if not order: raise HTTPException(status_code=404, detail="Purchase Order not found.")
    current = list(order.line_items)
    if payload.order_number.strip() != order.order_number and db.query(StorePurchaseOrder).filter(StorePurchaseOrder.order_number == payload.order_number.strip(), StorePurchaseOrder.id != order_id).first():
        raise HTTPException(status_code=409, detail="Order number already exists.")
    subtotal = round(sum(line.quantity * line.unit_rate for line in payload.items), 2)
    gst_amount = round(subtotal * payload.gst_rate / 100, 2)
    if order.status in {"Approved", "Completed", "Partially Received"}:
        raise HTTPException(status_code=409, detail="An approved or received Purchase Order cannot be edited.")
    order.order_number = payload.order_number.strip(); order.supplier = payload.supplier.strip(); order.po_type = payload.po_type
    detail = order.details
    for key, value in payload.model_dump(exclude={"order_number", "supplier", "po_type", "items", "status"}).items(): setattr(detail, key, value)
    detail.subtotal = subtotal; detail.gst_amount = gst_amount; detail.grand_total = round(subtotal + gst_amount + payload.round_off, 2)
    db.query(StorePurchaseOrderLine).filter_by(order_id=order_id).delete(synchronize_session=False)
    db.add_all([
        StorePurchaseOrderLine(order_id=order.id, line_number=index, item_description=line.item_description.strip(),
            quantity=line.quantity, uom=line.uom.strip() or "Nos", unit_rate=line.unit_rate,
            amount=round(line.quantity * line.unit_rate, 2))
        for index, line in enumerate(payload.items, start=1)
    ])
    order.item_name = payload.items[0].item_description.strip()
    order.quantity = sum(line.quantity for line in payload.items)
    order.unit_price = payload.items[0].unit_rate
    db.add(StorePurchaseOrderActivity(
        order_id=order.id,
        action="Updated",
        details="Purchase Order details updated.",
        performed_by=user.name,
    ))
    db.commit(); db.refresh(order)
    log_activity(db, "Store PO Updated", "StorePurchaseOrder", f"Updated Store PO {order.order_number}.", user.name, entity_id=order.id, entity_name=order.order_number, workspace="Store")
    return _order_response(order)

@router.post("/purchase-orders/{order_id}/complete")
def complete_order(order_id: int, user: User = Depends(store_admin), db: Session = Depends(get_db)):
    """Close the PO after goods are received. This changes only the PO
    workflow status; Items/Stock remain an independent manual workflow."""
    order = db.get(StorePurchaseOrder, order_id)
    if not order: raise HTTPException(status_code=404, detail="Purchase Order not found.")
    order.status = "Completed"
    db.add(StorePurchaseOrderActivity(
        order_id=order.id,
        action="Completed",
        details="Purchase Order marked as completed.",
        performed_by=user.name,
    ))
    db.commit(); db.refresh(order)
    log_activity(db, "Store PO Completed", "StorePurchaseOrder", f"Store PO {order.order_number} marked as completed.", user.name, entity_id=order.id, entity_name=order.order_number, workspace="Store")
    return _order_response(order)

@router.get("/reports")
def reports(_: User = Depends(store_user), db: Session = Depends(get_db)):
    return {"orders": db.query(StorePurchaseOrder).count(), "inventory": db.query(StorePurchaseItem).count()}


def _order_response(order: StorePurchaseOrder):
    detail = order.details
    received_quantity = sum(
        float(line.received_quantity or 0)
        for receipt in order.material_receipts
        for line in receipt.lines
    )
    return {
        "id": order.id, "order_number": order.order_number, "supplier": order.supplier,
        "status": order.status, "po_type": order.po_type, "created_at": order.created_at, "created_by": order.created_by,
        "received_quantity": received_quantity,
        "activities": [{
            "id": activity.id, "action": activity.action, "details": activity.details,
            "performed_by": activity.performed_by, "created_at": activity.created_at,
        } for activity in order.activities],
        "approval_history": [{"id": approval.id, "level_id": approval.level_id, "level": approval.level_name,
            "level_order": approval.level_order, "rule": approval.approval_rule, "approver_id": approval.approver_id,
            "approver_name": approval.approver.name, "action": approval.action, "action_at": approval.action_at,
            "rejection_reason": approval.rejection_reason} for approval in order.approvals],
        "vendor_code": detail.vendor_code if detail else None,
        "vendor_address": detail.vendor_address if detail else None,
        "vendor_contact": detail.vendor_contact if detail else None,
        "vendor_gst": detail.vendor_gst if detail else None,
        "po_date": detail.po_date if detail else None,
        "reference": detail.reference if detail else None,
        "delivery_address": detail.delivery_address if detail else None,
        "gst_rate": detail.gst_rate if detail else 0,
        "subtotal": detail.subtotal if detail else 0,
        "gst_amount": detail.gst_amount if detail else 0,
        "round_off": detail.round_off if detail else 0,
        "grand_total": detail.grand_total if detail else 0,
        "price_basis": detail.price_basis if detail else None,
        "packing_terms": detail.packing_terms if detail else None,
        "freight_terms": detail.freight_terms if detail else None,
        "insurance_terms": detail.insurance_terms if detail else None,
        "delivery_terms": detail.delivery_terms if detail else None,
        "inspection_terms": detail.inspection_terms if detail else None,
        "warranty_terms": detail.warranty_terms if detail else None,
        "payment_terms": detail.payment_terms if detail else None,
        "quantity_variance": detail.quantity_variance if detail else None,
        "notes": detail.notes if detail else None,
        "items": [{"id": line.id, "line_number": line.line_number, "item_description": line.item_description,
                    "quantity": line.quantity, "uom": line.uom, "unit_rate": line.unit_rate, "amount": line.amount}
                   for line in order.line_items],
    }


def _material_receipt_response(receipt: StorePurchaseMaterialReceipt):
    detail = receipt.order.details
    taxable_amount = round(sum((line.amount or 0) for line in receipt.lines), 2)
    gst_rate = detail.gst_rate if detail else 0
    freight_charge = round(receipt.freight_charge or 0, 2)
    # Freight is added to the received goods value first; GST is then
    # calculated on that receipt total, matching the Material Received form.
    gst_amount = round((taxable_amount + freight_charge) * gst_rate / 100, 2)
    return {
        "id": receipt.id, "order_id": receipt.order_id, "order_number": receipt.order.order_number,
        "vendor_name": receipt.vendor_name, "reference": receipt.reference, "vendor_code": receipt.vendor_code,
        "invoice_date": receipt.invoice_date, "invoice_number": receipt.invoice_number,
        "bill_file_url": receipt.bill_file_url, "remarks": receipt.remarks,
        "created_at": receipt.created_at, "created_by": receipt.created_by,
        "updated_at": receipt.updated_at, "updated_by": receipt.updated_by,
        "payment_status": receipt.payment_status or "Unpaid",
        "amount_paid": round(receipt.amount_paid or 0, 2),
        "payment_date": receipt.payment_date,
        "payment_reference": receipt.payment_reference,
        "gst_rate": gst_rate, "taxable_amount": taxable_amount, "freight_charge": freight_charge, "gst_amount": gst_amount,
        "invoice_total": round(taxable_amount + freight_charge + gst_amount, 2),
        "lines": [{"id": line.id, "line_number": line.line_number, "item_description": line.item_description,
                   "ordered_quantity": line.ordered_quantity, "received_quantity": line.received_quantity,
                   "uom": line.uom, "unit_rate": line.unit_rate, "amount": line.amount} for line in receipt.lines],
    }


class StoreVendorPayload(BaseModel):
    vendor_name: str
    person_name: str | None = None
    vendor_gst: str | None = None
    contact: str | None = None
    address: str | None = None
    items_supplied: list[str] | str | None = None
    status: str = "Active"


def _serialize_vendor(v: StoreVendor):
    items = []
    if v.items_supplied:
        try:
            items = json.loads(v.items_supplied)
            if not isinstance(items, list):
                items = [str(items)]
        except Exception:
            items = [x.strip() for x in v.items_supplied.split(",") if x.strip()]
    return {
        "id": v.id,
        "vendor_name": v.vendor_name,
        "person_name": v.person_name,
        "vendor_gst": v.vendor_gst,
        "contact": v.contact,
        "address": v.address,
        "items_supplied": items,
        "status": v.status or "Active",
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "updated_at": v.updated_at.isoformat() if v.updated_at else None,
    }


@router.get("/vendors")
def list_vendors(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_application_access(user, db, "store_purchase")
    vendors = db.query(StoreVendor).order_by(StoreVendor.vendor_name.asc()).all()
    return [_serialize_vendor(v) for v in vendors]


@router.post("/vendors")
def create_vendor(payload: StoreVendorPayload, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_application_access(user, db, "store_purchase", admin=True)
    if not payload.vendor_name.strip():
        raise HTTPException(status_code=400, detail="Vendor name is required.")
    items_str = ""
    if isinstance(payload.items_supplied, list):
        items_str = json.dumps(payload.items_supplied)
    elif isinstance(payload.items_supplied, str):
        items_str = payload.items_supplied.strip()

    vendor = StoreVendor(
        vendor_name=payload.vendor_name.strip(),
        person_name=payload.person_name.strip() if payload.person_name else None,
        vendor_gst=payload.vendor_gst.strip() if payload.vendor_gst else None,
        contact=payload.contact.strip() if payload.contact else None,
        address=payload.address.strip() if payload.address else None,
        items_supplied=items_str,
        status=payload.status or "Active",
    )
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    log_activity(db, "Vendor Created", "StoreVendor", f"Created vendor '{vendor.vendor_name}'.", user.name, entity_id=vendor.id, entity_name=vendor.vendor_name, workspace="Store")
    return _serialize_vendor(vendor)


@router.put("/vendors/{vendor_id}")
def update_vendor(vendor_id: int, payload: StoreVendorPayload, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_application_access(user, db, "store_purchase", admin=True)
    vendor = db.get(StoreVendor, vendor_id)
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found.")
    if payload.vendor_name:
        vendor.vendor_name = payload.vendor_name.strip()
    vendor.person_name = payload.person_name.strip() if payload.person_name else None
    vendor.vendor_gst = payload.vendor_gst.strip() if payload.vendor_gst else None
    vendor.contact = payload.contact.strip() if payload.contact else None
    vendor.address = payload.address.strip() if payload.address else None
    if payload.items_supplied is not None:
        if isinstance(payload.items_supplied, list):
            vendor.items_supplied = json.dumps(payload.items_supplied)
        else:
            vendor.items_supplied = str(payload.items_supplied).strip()
    if payload.status:
        vendor.status = payload.status
    db.commit()
    db.refresh(vendor)
    log_activity(db, "Vendor Updated", "StoreVendor", f"Updated vendor '{vendor.vendor_name}'.", user.name, entity_id=vendor.id, entity_name=vendor.vendor_name, workspace="Store")
    return _serialize_vendor(vendor)


@router.delete("/vendors/{vendor_id}")
def delete_vendor(vendor_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_application_access(user, db, "store_purchase", admin=True)
    vendor = db.get(StoreVendor, vendor_id)
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found.")
    vendor_name = vendor.vendor_name
    db.delete(vendor)
    db.commit()
    log_activity(db, "Vendor Deleted", "StoreVendor", f"Deleted vendor '{vendor_name}'.", user.name, entity_name=vendor_name, workspace="Store")
    return {"message": "Vendor deleted successfully."}
