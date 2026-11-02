import json







from datetime import date, datetime, timedelta







import re







from typing import Optional



from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Query, Body







from pydantic import BaseModel, Field







from sqlalchemy.orm import Session, object_session







from sqlalchemy import func







from app.database import get_db







from app.models.models import StoreVendor, StorePurchaseItem, StorePurchaseStockItem, StorePurchaseOrder, StorePurchaseOrderActivity, StorePurchaseOrderDetail, StorePurchaseOrderLine, StorePurchaseMaterialReceipt, StorePurchaseMaterialReceiptLine, StorePurchaseStockTransaction, StorePurchaseStockTransfer, StorePOItemMasterItem, User, POApprovalLevel, POApprovalLevelApprover, StorePurchaseOrderApproval, POPreparedByConfig, POPreparedBySignature, StorePONotification







from app.utils.auth import application_role, get_current_user, require_application_access, require_super_admin







from app.utils.helpers import log_activity







from app.routers.upload_helpers import read_upload_bytes, save_upload_bytes















router = APIRouter(prefix="/api/store-purchase", tags=["Store Purchase"])















class ItemPayload(BaseModel):







    name: str







    quantity: float = 0







    reorder_level: float = 0







    out_of_stock_limit: float = 0







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















class StockLimitPayload(BaseModel):







    out_of_stock_limit: float = Field(default=0.0, ge=0)







    low_stock_limit: float = Field(default=0.0, ge=0)















class BatchStockLimitItem(BaseModel):







    item_id: int







    out_of_stock_limit: float = Field(default=0.0, ge=0)







    low_stock_limit: float = Field(default=0.0, ge=0)















class BatchStockLimitPayload(BaseModel):







    items: list[BatchStockLimitItem]















def _calculate_status(quantity: float, out_of_stock_limit: float | None = 0.0, low_stock_limit: float | None = 0.0) -> str:







    qty = float(quantity or 0.0)







    out_limit = 0.0 if out_of_stock_limit is None else float(out_of_stock_limit)







    low_limit = 0.0 if low_stock_limit is None else float(low_stock_limit)















    # 0 hone par automatic out of stock ho jaye OR if qty <= out_of_stock_limit:







    effective_out_limit = max(0.0, out_limit)







    if qty <= 0.0 or qty <= effective_out_limit:







        return "Out of Stock"















    # Low Stock: Item thoda bacha hai (qty <= low_stock_limit)







    if low_limit > effective_out_limit and qty <= low_limit:







        return "Low Stock"















    # In Stock: Item available hai







    return "In Stock"















def _status(quantity: float, out_of_stock_limit: float | None = 0.0, low_stock_limit: float | None = 0.0) -> str:







    return _calculate_status(quantity, out_of_stock_limit, low_stock_limit)























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















def _same_inventory_item(db: Session, group_name: str, stock_item: str | None, rate: float | None = None):
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

    rate = float(item.rate or 0.0)
    receipt_txs = [tx for tx in getattr(item, "transactions", []) if tx.transaction_type == "RECEIPT"]
    if receipt_txs:
        distinct_rates = list(dict.fromkeys(round(float(tx.rate or 0), 2) for tx in receipt_txs if (tx.rate or 0) > 0))
        if len(distinct_rates) > 1:
            rate = sum(distinct_rates)
        elif len(distinct_rates) == 1:
            rate = distinct_rates[0]
        tot_amt = sum(float(tx.amount if tx.amount is not None else ((tx.quantity or 0) * (tx.rate or 0))) for tx in receipt_txs)
        remaining_stock_amount = round(tot_amt, 2)
    else:
        remaining_stock_amount = round((item.quantity or 0) * rate, 2)

    return {
        "id": item.id, "name": item.name, "quantity": item.quantity,
        "available_quantity": item.quantity, "total_stock": item.total_received_quantity or item.quantity,
        "total_quantity": item.total_received_quantity or item.quantity,
        "previous_quantity": previous_quantity, "current_month_quantity": current_month_quantity,
        "issued_quantity": item.issued_quantity,
        "remaining_quantity": item.quantity, "remaining_stock_amount": remaining_stock_amount,







        "reorder_level": item.reorder_level, "out_of_stock_limit": float(getattr(item, "out_of_stock_limit", 0.0) or 0.0), "vendor_name": item.vendor_name, "reference_no": item.reference_no,







        "receipt_date": item.receipt_date, "stock_item": item.stock_item, "uom": item.uom,







        "location": item.location, "required_for": item.required_for, "rate": rate,







        "low_stock_limit": float(item.reorder_level or 0.0), "status": _calculate_status(item.quantity or 0, getattr(item, "out_of_stock_limit", 0.0), getattr(item, "reorder_level", 0.0)), "created_at": item.created_at,







        "stock_items": [{"id": stock.id, "name": stock.name, "uom": stock.uom, "location": stock.location,
                         "vendor_name": stock.vendor_name,
                         "rate": 0.0 if ((stock.available_quantity or 0) <= 0 and (stock.total_received_quantity or 0) <= 0) else (rate if (len(item.stock_items) <= 1) else (stock.rate or rate)),
                         "total_received_quantity": stock.total_received_quantity,
                         "issued_quantity": stock.issued_quantity,
                         "available_quantity": stock.available_quantity,
                         "amount": 0.0 if ((stock.available_quantity or 0) <= 0 and (stock.total_received_quantity or 0) <= 0) else (remaining_stock_amount if (len(item.stock_items) <= 1) else round((stock.available_quantity or 0) * (stock.rate or rate), 2))} for stock in item.stock_items],







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







    item = existing_item or _same_inventory_item(db, normalized, normalized_stock_item, rate=rate)







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







        old_qty = float(item.quantity - quantity)
        old_rate = float(item.rate or 0)
        new_qty = float(quantity or 0)
        new_rate = float(rate or 0)
        if old_qty > 0 and old_rate > 0 and new_rate > 0:
            item.rate = round(((old_qty * old_rate) + (new_qty * new_rate)) / item.quantity, 2)
        elif new_rate > 0:
            item.rate = new_rate







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







    item.status = _calculate_status(item.quantity, getattr(item, "out_of_stock_limit", 0.0), getattr(item, "reorder_level", 0.0))







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







    remark_1: str | None = None







    remark_2: str | None = None







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



    remarks: str | None = None















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











def _approval_levels_for_level(db: Session, level: POApprovalLevel):



    query = db.query(POApprovalLevel).filter(



        func.lower(POApprovalLevel.name) == level.name.strip().lower(),



        POApprovalLevel.min_amount == level.min_amount,



        POApprovalLevel.approval_rule == level.approval_rule,



    )



    query = query.filter(POApprovalLevel.max_amount.is_(None) if level.max_amount is None



                         else POApprovalLevel.max_amount == level.max_amount)



    return query.order_by(POApprovalLevel.id).all()











def _stage_signature_configs(level_group):



    first_configs = [lv for lv in level_group if lv.checked_by_user_id]



    final_configs = [lv for lv in level_group if lv.approved_by_user_id]



    first_ids = {lv.checked_by_user_id for lv in first_configs}



    final_ids = {lv.approved_by_user_id for lv in final_configs}



    if len(first_ids) > 1 or len(final_ids) > 1:



        raise HTTPException(status_code=422, detail="A matching amount slab has conflicting Approved By or Final Approved By users. Please correct the PO signature configuration.")



    return (first_configs[0] if first_configs else None, next(iter(first_ids), None),



            final_configs[0] if final_configs else None, next(iter(final_ids), None))











def _assign_first_approval_stages(order: StorePurchaseOrder, applicable_levels, db: Session):



    """Snapshot only the first approver now; final assignment waits for approval."""



    next_stage = 1



    for level_group in applicable_levels:



        primary = level_group[0]



        first_config, checked_by_user_id, final_config, final_user_id = _stage_signature_configs(level_group)



        if not checked_by_user_id:



            raise HTTPException(status_code=422, detail=f"Configure the Approved By user for amount-wise approval level '{primary.name}'.")



        if checked_by_user_id == final_user_id:



            raise HTTPException(status_code=422, detail=f"Approved By and Final Approved By must be different users for '{primary.name}'.")



        # The signature configuration is the authoritative assignment for the



        # two named stages; generic amount-slab approvers cannot substitute.



        first_ids, first_rule, first_role = {checked_by_user_id}, "any", "checked_by"



        first_signature = first_config.checked_by_signature_url if first_config else None



        first_users = {u.id: u.name for u in db.query(User).filter(User.id.in_(first_ids)).all()} if first_ids else {}



        final_user = db.get(User, final_user_id) if final_user_id else None



        for approver_id in first_ids:



            db.add(StorePurchaseOrderApproval(



                order_id=order.id, level_id=primary.id, level_name=primary.name,



                level_order=next_stage, approval_cycle=order.approval_cycle,



                approval_rule=first_rule, approver_id=approver_id,



                approver_name=first_users.get(approver_id), workflow_role=first_role,



                signature_url=first_signature,



                next_approver_id=final_user_id,



                next_approver_name=final_user.name if final_user else None,



                next_signature_url=final_config.approved_by_signature_url if final_config else None,



            ))



        next_stage += 1



        if final_user_id:



            next_stage += 1















def _set_current_approval_status(order: StorePurchaseOrder):







    grouped = {}







    cycle_rows = [a for a in order.approvals if a.approval_cycle == order.approval_cycle]



    for approval in cycle_rows:







        grouped.setdefault(approval.level_order, []).append(approval)







    for _, rows in sorted(grouped.items()):







        if any(row.action == "Rejected" for row in rows):



            rejected_by = "Final Approved By" if any(row.workflow_role == "approved_by" and row.action == "Rejected" for row in rows) else "Approved By"



            order.status = f"Rejected by {rejected_by}"; return







        approved = any(row.action == "Approved" for row in rows) if rows[0].approval_rule == "any" else all(row.action == "Approved" for row in rows)







        if not approved:







            order.status = "Partially Approved" if any(r.action == "Approved" for earlier in grouped.values() for r in earlier) else "Pending Approval"







            return







        # A configured final approver is required but is not assigned until the



        # first stage completes. Keep the PO open while that handoff is pending.



        if rows[0].next_approver_id and not any(r.approval_cycle == order.approval_cycle and r.level_order == rows[0].level_order + 1 for r in cycle_rows):



            order.status = "Partially Approved"



            return







    order.status = "Finally Approved"











def _forward_to_next_approver(order: StorePurchaseOrder, acted_row: StorePurchaseOrderApproval, db: Session):



    """Create the final approver assignment only once the first stage is complete."""



    stage_rows = [r for r in order.approvals if r.approval_cycle == order.approval_cycle and r.level_order == acted_row.level_order]



    if not stage_rows or not stage_rows[0].next_approver_id:



        return



    if any(r.action == "Rejected" for r in stage_rows):



        return



    complete = (any(r.action == "Approved" for r in stage_rows)



                if stage_rows[0].approval_rule == "any"



                else all(r.action == "Approved" for r in stage_rows))



    if not complete:



        return



    next_stage = acted_row.level_order + 1



    if any(r.approval_cycle == order.approval_cycle and r.level_order == next_stage for r in order.approvals):



        return



    source = next((r for r in stage_rows if r.next_approver_id), stage_rows[0])



    final_user_id = source.next_approver_id



    if not final_user_id:



        level = db.get(POApprovalLevel, source.level_id)



        if level and level.approved_by_user_id:



            final_user_id = level.approved_by_user_id



    if not final_user_id:



        return



    final_user = db.get(User, final_user_id)



    final_sig_url = source.next_signature_url



    if not final_sig_url:



        level = db.get(POApprovalLevel, source.level_id)



        if level:



            final_sig_url = level.approved_by_signature_url



    next_row = StorePurchaseOrderApproval(



        order_id=order.id, level_id=source.level_id,



        level_name=f"{source.level_name} — Final", level_order=next_stage,



        approval_rule="any", approver_id=final_user_id,



        approver_name=final_user.name if final_user else source.next_approver_name,



        workflow_role="approved_by",



        signature_url=final_sig_url, approval_cycle=order.approval_cycle,



    )



    order.approvals.append(next_row)



    db.add(next_row)



    db.add(StorePurchaseOrderActivity(



        order_id=order.id, action="Forwarded for Final Approval",



        details=f"Forwarded to Final Approved By: {source.next_approver_name or source.next_approver_id}.",



        performed_by=source.approver_name or "Approval Workflow",



    ))



    db.flush()



    return final_user_id











def is_order_prepared_by(order: StorePurchaseOrder, user: User, db: Session = None) -> bool:



    if order.created_by_user_id is not None:



        return order.created_by_user_id == user.id



    if order.created_by:



        return order.created_by.strip().lower() == user.name.strip().lower()



    return False











def _creator_user_ids(order: StorePurchaseOrder, db: Session) -> list[int]:
    if order.created_by_user_id:
        return [order.created_by_user_id]
    if order.created_by:
        u = db.query(User).filter(func.lower(User.name) == order.created_by.strip().lower(), User.is_active == True).first()
        if u:
            return [u.id]
    return []











def _is_final_user_misassigned_to_first_stage(approval: StorePurchaseOrderApproval, db: Session) -> bool:



    if approval.workflow_role == "approved_by":



        return False



    final_user_ids = {approval.next_approver_id} if approval.next_approver_id else set()



    level = db.get(POApprovalLevel, approval.level_id)



    if level:



        final_user_ids.update(lv.approved_by_user_id for lv in _approval_levels_for_level(db, level) if lv.approved_by_user_id)



    return approval.approver_id in final_user_ids











def _repair_legacy_first_stage(order: StorePurchaseOrder, db: Session) -> bool:



    """Move a pending final-user fallback assignment off the first stage."""



    changed = False



    groups = {}



    for approval in order.approvals:



        if approval.approval_cycle == order.approval_cycle:



            groups.setdefault(approval.level_order, []).append(approval)



    for rows in groups.values():



        invalid = [r for r in rows if r.action == "Pending" and _is_final_user_misassigned_to_first_stage(r, db)]



        if not invalid:



            continue



        existing_first_stage = [r for r in rows if r.action == "Pending" and r not in invalid]



        if existing_first_stage:



            final_user_id = invalid[0].approver_id or invalid[0].next_approver_id



            for first_row in existing_first_stage:



                if not first_row.next_approver_id:



                    first_row.next_approver_id = final_user_id



                    final_user = db.get(User, final_user_id) if final_user_id else None



                    first_row.next_approver_name = final_user.name if final_user else None



            for duplicate in invalid:



                db.delete(duplicate)



            changed = True



            continue



        template = invalid[0]



        level = db.get(POApprovalLevel, template.level_id)



        if not level:



            continue



        level_group = _approval_levels_for_level(db, level)



        first_config, checked_by_user_id, final_config, configured_final_id = _stage_signature_configs(level_group)



        final_user_id = configured_final_id or template.next_approver_id



        first_user_id = checked_by_user_id if checked_by_user_id != final_user_id else None



        first_role = "checked_by"



        if not first_user_id:



            continue



        first_user = db.get(User, first_user_id)



        if not first_user:



            continue



        template.approver_id = first_user.id



        template.approver_name = first_user.name



        template.workflow_role = first_role



        template.signature_url = first_config.checked_by_signature_url if first_role == "checked_by" and first_config else None



        template.approval_rule = "any" if first_role == "checked_by" else level.approval_rule



        template.next_approver_id = final_user_id



        final_user = db.get(User, final_user_id) if final_user_id else None



        template.next_approver_name = final_user.name if final_user else None



        template.next_signature_url = final_config.approved_by_signature_url if final_config else level.approved_by_signature_url



        for duplicate in invalid[1:]:



            db.delete(duplicate)



        changed = True



    return changed











def _notify_po_user(user_id: int | None, title: str, message: str, order: StorePurchaseOrder, db=None):
    if not user_id:
        return
    from app import notifications
    notifications.notify_user(user_id, title, message, order_id=order.id)
    if db is not None:
        try:
            existing = db.query(StorePONotification).filter(
                StorePONotification.user_id == user_id,
                StorePONotification.order_id == order.id,
                StorePONotification.title == title,
                StorePONotification.is_read == False,
            ).first()
            if not existing:
                db.add(StorePONotification(user_id=user_id, order_id=order.id, title=title, message=message, is_read=False))
        except Exception:
            pass















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







    orders = db.query(StorePurchaseOrder).filter(







        StorePurchaseOrder.is_deleted == False,







        StorePurchaseOrder.status.in_(["Pending Approval", "Partially Approved"])







    ).all()







    result = []



    repaired_assignments = False







    for order in orders:







        repaired_assignments = _repair_legacy_first_stage(order, db) or repaired_assignments







        stages = {}







        for row in order.approvals:



            if row.approval_cycle == order.approval_cycle:



                stages.setdefault(row.level_order, []).append(row)







        for _, rows in sorted(stages.items()):







            # Older submissions may have snapshotted the final user as a



            # first-stage fallback approver. Do not expose that assignment as



            # actionable or let it block the remaining first-stage approvers.



            rows = [x for x in rows if not (x.action == "Pending" and _is_final_user_misassigned_to_first_stage(x, db))]



            if not rows:



                break







            complete = any(x.action == "Approved" for x in rows) if rows[0].approval_rule == "any" else all(x.action == "Approved" for x in rows)







            if not complete:







                if any(x.approver_id == user.id and x.action == "Pending" for x in rows): result.append(_order_response(order))







                break







    if repaired_assignments:



        db.commit()



    return result















@router.post("/purchase-orders/{order_id}/approval")







def act_on_approval(order_id: int, payload: ApprovalActionPayload, user: User = Depends(store_user), db: Session = Depends(get_db)):







    if payload.action not in {"Approved", "Rejected"}: raise HTTPException(status_code=422, detail="Action must be Approved or Rejected.")







    if payload.action == "Rejected" and not (payload.rejection_reason or "").strip(): raise HTTPException(status_code=422, detail="A rejection reason is required.")







    order = db.get(StorePurchaseOrder, order_id)







    if not order or order.status not in {"Pending Approval", "Partially Approved"}: raise HTTPException(status_code=409, detail="This Purchase Order is not awaiting approval.")







    if _repair_legacy_first_stage(order, db):



        db.commit()



        db.refresh(order)







    row = db.query(StorePurchaseOrderApproval).filter_by(order_id=order_id, approval_cycle=order.approval_cycle,



        approver_id=user.id, action="Pending").order_by(StorePurchaseOrderApproval.level_order).first()







    if row and _is_final_user_misassigned_to_first_stage(row, db):



        row = None







    if not row: raise HTTPException(status_code=403, detail="You are not assigned as an approver for this Purchase Order.")







    # Later stages cannot be actioned before all earlier rules are satisfied.







    prior = [x for x in order.approvals if x.approval_cycle == order.approval_cycle and x.level_order < row.level_order]







    for stage in set(x.level_order for x in prior):







        group = [x for x in prior if x.level_order == stage]







        complete = any(x.action == "Approved" for x in group) if group[0].approval_rule == "any" else all(x.action == "Approved" for x in group)







        if not complete: raise HTTPException(status_code=409, detail="The previous approval level must be completed first.")







    current_stage = [x for x in order.approvals if x.approval_cycle == order.approval_cycle and x.level_order == row.level_order]



    current_complete = any(x.action == "Approved" for x in current_stage) if current_stage[0].approval_rule == "any" else all(x.action == "Approved" for x in current_stage)



    if current_complete: raise HTTPException(status_code=409, detail="This approval stage is already complete.")







    row.action = payload.action; row.action_at = datetime.utcnow(); row.rejection_reason = (payload.rejection_reason or "").strip() or None



    row.remarks = (payload.remarks or "").strip() or None







    forwarded_to_id = _forward_to_next_approver(order, row, db)



    _set_current_approval_status(order)







    db.add(StorePurchaseOrderActivity(order_id=order.id, action=f"Approval {payload.action}", details=f"{row.level_name}" + (f": {row.rejection_reason}" if row.rejection_reason else ""), performed_by=user.name))







    db.commit(); db.refresh(order)







    # Mark any earlier pending notifications for this order as read so the actor never sees stale popups
    db.query(StorePONotification).filter(
        StorePONotification.order_id == order.id,
        StorePONotification.title.in_([
            "New Purchase Order Pending Approval",
            "Purchase Order Awaiting Final Approval",
            "Purchase Order Resubmitted",
        ]),
        StorePONotification.is_read == False,
    ).update({"is_read": True}, synchronize_session=False)

    creator_user = db.get(User, order.created_by_user_id) if order.created_by_user_id else None
    creator_name = (order.created_by or (creator_user.name if creator_user else "")).strip().lower()

    # Find all user IDs associated with Prepared By (by ID and name)
    creator_user_ids = set()
    if order.created_by_user_id:
        creator_user_ids.add(order.created_by_user_id)
    if creator_name:
        for u in db.query(User).filter(func.lower(User.name) == creator_name, User.is_active == True).all():
            creator_user_ids.add(u.id)

    # Primary ID to notify for Prepared By (exactly ONE notification sent to Prepared By)
    primary_creator_id = order.created_by_user_id or (list(creator_user_ids)[0] if creator_user_ids else None)

    if payload.action == "Rejected":
        rejected_by = "Final Approved By" if row.workflow_role == "approved_by" else "Approved By"
        # 1. Notify Prepared By ONLY if Prepared By is NOT the person who rejected it!
        if primary_creator_id and primary_creator_id != user.id:
            _notify_po_user(primary_creator_id, "Purchase Order Rejected",
                f"PO {order.order_number} was rejected by {rejected_by}. Reason: {row.rejection_reason}", order, db=db)
        
        # 2. When Final Approved By rejects, notify Approved By (stage 1 approvers)
        # Exclude: creator (already notified above), AND user.id (the person who rejected it)!
        if row.workflow_role == "approved_by":
            stage1 = [a for a in order.approvals if a.approval_cycle == order.approval_cycle and (a.level_order == 1 or a.workflow_role in {"checked_by", "approver"})]
            notified_stage1 = set()
            for a in stage1:
                if not a.approver_id or a.approver_id in notified_stage1:
                    continue
                # If approver is the person who rejected it, NEVER notify them!
                if a.approver_id == user.id:
                    continue
                # If approver is the creator, skip (already handled)
                if a.approver_id in creator_user_ids or (creator_name and (a.approver_name or "").strip().lower() == creator_name):
                    continue
                notified_stage1.add(a.approver_id)
                _notify_po_user(a.approver_id, "Purchase Order Rejected",
                    f"PO {order.order_number} was rejected by Final Approved By. Reason: {row.rejection_reason}", order, db=db)

    elif order.status == "Finally Approved":
        # 1. Notify Prepared By (single notification, skip if creator is the actor)
        if primary_creator_id and primary_creator_id != user.id:
            _notify_po_user(primary_creator_id, "Purchase Order Finally Approved", f"PO {order.order_number} is finally approved.", order, db=db)
        
        # 2. Notify Approved By (stage 1 approvers), excluding actor and creator
        stage1 = [a for a in order.approvals if a.approval_cycle == order.approval_cycle and (a.level_order == 1 or a.workflow_role in {"checked_by", "approver"})]
        notified_stage1 = set()
        for a in stage1:
            if not a.approver_id or a.approver_id in notified_stage1:
                continue
            if a.approver_id == user.id:
                continue
            if a.approver_id in creator_user_ids or (creator_name and (a.approver_name or "").strip().lower() == creator_name):
                continue
            notified_stage1.add(a.approver_id)
            _notify_po_user(a.approver_id, "Purchase Order Finally Approved", f"PO {order.order_number} is finally approved.", order, db=db)

    elif forwarded_to_id:



        _notify_po_user(forwarded_to_id, "Purchase Order Awaiting Final Approval",



            f"PO {order.order_number} was approved by Approved By and is awaiting your final approval.", order, db=db)







    rejection_notice = f" PO creator {order.created_by} notified with reason: {row.rejection_reason}." if payload.action == "Rejected" else ""



    log_activity(db, f"Store PO {payload.action}", "StorePurchaseOrder", f"Store PO {order.order_number} {payload.action.lower()} by {user.name}.{rejection_notice}", user.name, entity_id=order.id, entity_name=order.order_number, workspace="Store")







    return _order_response(order, db=db)

















@router.get("/my-po-notifications")
def my_po_notifications(user: User = Depends(store_user), db: Session = Depends(get_db)):
    """Return all unread persistent PO notifications for the current user."""
    rows = (
        db.query(StorePONotification)
        .filter(StorePONotification.user_id == user.id, StorePONotification.is_read == False)
        .order_by(StorePONotification.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": r.id,
            "order_id": r.order_id,
            "title": r.title,
            "message": r.message,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/my-po-notifications/mark-read")
def mark_po_notifications_read(
    payload: dict = Body(default={}),
    user: User = Depends(store_user),
    db: Session = Depends(get_db),
):
    """Mark specific notification ids (or all unread) as read."""
    ids = (payload or {}).get("ids")
    q = db.query(StorePONotification).filter(
        StorePONotification.user_id == user.id,
        StorePONotification.is_read == False,
    )
    if ids and len(ids) > 0:
        q = q.filter(StorePONotification.id.in_(ids))
    q.update({"is_read": True}, synchronize_session=False)
    db.commit()
    return {"ok": True}


@router.get("/dashboard")







def dashboard(_: User = Depends(store_user), db: Session = Depends(get_db)):







    items = db.query(StorePurchaseItem).all()







    return {"item_count": len(items), "low_stock_count": sum(1 for i in items if i.quantity <= i.reorder_level),







            "purchase_order_count": db.query(StorePurchaseOrder).filter(StorePurchaseOrder.is_deleted == False).count()}















def _merge_same_items(db: Session):
    """Ensure items with the same Group Name and Items Name are consolidated into a single row."""
    all_items = db.query(StorePurchaseItem).order_by(StorePurchaseItem.id.asc()).all()
    groups = {}
    for it in all_items:
        key = (it.name.strip().lower(), (it.stock_item or "General").strip().lower())
        groups.setdefault(key, []).append(it)

    needs_commit = False
    for key, items in groups.items():
        if len(items) > 1:
            primary = items[0]
            for duplicate in items[1:]:
                # Move transactions to primary
                db.query(StorePurchaseStockTransaction).filter(
                    StorePurchaseStockTransaction.item_id == duplicate.id
                ).update({"item_id": primary.id})

                # Delete duplicate stock item child records
                db.query(StorePurchaseStockItem).filter(
                    StorePurchaseStockItem.material_id == duplicate.id
                ).delete()

                # Consolidate quantities into primary
                primary.quantity = (primary.quantity or 0) + (duplicate.quantity or 0)
                primary.total_received_quantity = (primary.total_received_quantity or 0) + (duplicate.total_received_quantity or 0)
                primary.current_month_quantity = (primary.current_month_quantity or 0) + (duplicate.current_month_quantity or 0)

                # Delete the duplicate item
                db.delete(duplicate)
                needs_commit = True

            db.flush()
            # Update primary stock item available quantity
            si = db.query(StorePurchaseStockItem).filter(StorePurchaseStockItem.material_id == primary.id).first()
            if si:
                si.total_received_quantity = primary.total_received_quantity
                si.available_quantity = primary.quantity

    if needs_commit:
        db.commit()

@router.get("/inventory")
def inventory(_: User = Depends(store_user), db: Session = Depends(get_db)):
    _merge_same_items(db)
    items = db.query(StorePurchaseItem).order_by(StorePurchaseItem.name).all()
    needs_commit = False
    for it in items:
        if (it.quantity or 0) <= 0 and (it.total_received_quantity or 0) <= 0:
            if (it.rate or 0) != 0:
                it.rate = 0.0
                needs_commit = True
            tx_count = db.query(StorePurchaseStockTransaction).filter(StorePurchaseStockTransaction.item_id == it.id).count()
            if tx_count == 0 and it.reference_no:
                it.reference_no = None
                it.vendor_name = None
                needs_commit = True
        else:
            rec_txs = [tx for tx in it.transactions if tx.transaction_type == "RECEIPT"]
            if rec_txs:
                distinct_rates = list(dict.fromkeys(round(float(tx.rate or 0), 2) for tx in rec_txs if (tx.rate or 0) > 0))
                calc_r = sum(distinct_rates) if distinct_rates else it.rate
                if it.rate != calc_r:
                    it.rate = calc_r
                    needs_commit = True
            for si in it.stock_items:
                if len(it.stock_items) <= 1 and si.rate != it.rate:
                    si.rate = it.rate
                    needs_commit = True
    if needs_commit:
        db.commit()
    return [_item_response(item) for item in items]















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







    item.out_of_stock_limit = getattr(payload, "out_of_stock_limit", 0.0) or 0.0







    item.status = _calculate_status(item.quantity, item.out_of_stock_limit, item.reorder_level)







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







    







    item.status = _calculate_status(item.quantity, getattr(item, "out_of_stock_limit", 0.0), getattr(item, "reorder_level", 0.0))







    db.commit(); db.refresh(item)







    log_activity(db, "Inventory Updated", "StoreInventory", f"Updated inventory item '{item.name}'.", _.name, entity_id=item.id, entity_name=item.name, workspace="Store")







    return _item_response(item)
















def _get_low_stock_recipient_ids(db) -> set:
    """Collect all user IDs who should receive low stock alerts:
    - Prepared By signatories
    - Checked By / Approved By users from all PO Approval Levels
    - All approvers listed in each level
    - Store Admins (super_admin flag + application access with admin role)
    """
    from app.models.models import Application, ApplicationAccess
    from sqlalchemy import func as _func

    recipient_ids = set()

    # 1. Prepared By signatories
    try:
        for pb in db.query(POPreparedBySignature).all():
            if pb.user_id:
                recipient_ids.add(pb.user_id)
    except Exception:
        pass

    # 2. Approval level users: checked_by, approved_by, and approvers list
    try:
        for lvl in db.query(POApprovalLevel).filter(POApprovalLevel.is_enabled == True).all():
            if lvl.checked_by_user_id:
                recipient_ids.add(lvl.checked_by_user_id)
            if lvl.approved_by_user_id:
                recipient_ids.add(lvl.approved_by_user_id)
            for approver in (lvl.approvers or []):
                if approver.user_id:
                    recipient_ids.add(approver.user_id)
    except Exception:
        pass

    # 3. Super admins
    try:
        for u in db.query(User).filter(User.is_super_admin == True, User.is_active == True).all():
            recipient_ids.add(u.id)
    except Exception:
        pass

    # 4. Store application admins
    try:
        store_app = db.query(Application).filter(
            _func.lower(Application.code) == "store"
        ).first()
        if store_app:
            for aa in db.query(ApplicationAccess).filter(
                ApplicationAccess.application_id == store_app.id,
                ApplicationAccess.role == "admin"
            ).all():
                if aa.user_id:
                    recipient_ids.add(aa.user_id)
    except Exception:
        pass

    return recipient_ids


def _send_low_stock_alerts(db, item: StorePurchaseItem) -> None:
    """Send a low stock / out-of-stock alert to all relevant users.
    Deduplicates: if an unread alert for the same item already exists for a user, skip.
    """
    try:
        from app import notifications as _notif

        qty = float(item.quantity or 0)
        reorder = float(item.reorder_level or 0)
        out_limit = float(getattr(item, "out_of_stock_limit", 0.0) or 0)

        # Determine if alert is needed
        is_out = qty <= 0 or (out_limit > 0 and qty <= out_limit)
        is_low = not is_out and reorder > 0 and qty <= reorder

        if not is_out and not is_low:
            return  # Stock is fine, no alert needed

        item_label = item.name or "Unknown Item"
        group_label = (item.stock_item or "").strip() or ""

        if is_out:
            title = "⚠️ Stock Alert: Out of Stock"
            message = (
                f"Item '{item_label}'"
                + (f" ({group_label})" if group_label else "")
                + f" is now OUT OF STOCK (Available: {qty:g} {item.uom or 'Nos'}). "
                + "Please raise a new Purchase Order immediately."
            )
        else:
            deficit = reorder - qty
            message = (
                f"Item '{item_label}'"
                + (f" ({group_label})" if group_label else "")
                + f" has LOW STOCK (Available: {qty:g} {item.uom or 'Nos'}, "
                + f"Reorder Level: {reorder:g} {item.uom or 'Nos'}, Deficit: {deficit:g}). "
                + "Please raise a new Purchase Order."
            )
            title = "⚠️ Stock Alert: Low Stock"

        recipient_ids = _get_low_stock_recipient_ids(db)

        for uid in recipient_ids:
            try:
                # Deduplicate: skip if unread alert for same item+title already exists
                existing = db.query(StorePONotification).filter(
                    StorePONotification.user_id == uid,
                    StorePONotification.title == title,
                    StorePONotification.is_read == False,
                    StorePONotification.order_id == None,
                ).filter(
                    StorePONotification.message.contains(item_label[:40])
                ).first()
                if not existing:
                    db.add(StorePONotification(
                        user_id=uid,
                        order_id=None,
                        title=title,
                        message=message,
                        is_read=False,
                    ))
                    # Also fire real-time SSE notification
                    _notif.notify_user(uid, title, message, order_id=None)
            except Exception:
                pass

        try:
            db.commit()
        except Exception:
            pass
    except Exception:
        pass

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







    item.status = _calculate_status(item.quantity, getattr(item, "out_of_stock_limit", 0.0), getattr(item, "reorder_level", 0.0))







    final_issued_to = issued_to or target_location







    final_location = target_location or item.location







    tx = StorePurchaseStockTransaction(item_id=item.id, transaction_type="ISSUE", quantity=payload.quantity,







        rate=item.rate or 0, amount=round(payload.quantity * (item.rate or 0), 2), transaction_date=issue_date,







        required_for=payload.required_for, issued_to=final_issued_to, location=final_location, remarks=payload.remarks,







        created_by=user.name)







    db.add(tx); db.commit(); db.refresh(item)

    # --- Low Stock Alert ---
    _send_low_stock_alerts(db, item)







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















def _stock_status_item_dict(item: StorePurchaseItem):







    limit = getattr(item, "out_of_stock_limit", 0.0)







    if limit is None:







        limit = 0.0







    low_limit = getattr(item, "reorder_level", 0.0)







    if low_limit is None:







        low_limit = 0.0







    full_name = item.name







    if item.stock_item and item.stock_item.strip() and item.stock_item.strip().lower() != "general":







        full_name = f"{item.name} - {item.stock_item.strip()}"







    return {







        "id": item.id,







        "group_name": item.name,







        "item_name": item.stock_item or "General",







        "full_item_name": full_name,







        "uom": item.uom or "Nos",







        "available_quantity": float(item.quantity or 0.0),







        "out_of_stock_limit": float(limit),







        "low_stock_limit": float(low_limit),







        "reorder_level": float(low_limit),







        "status": _calculate_status(item.quantity or 0.0, limit, low_limit),







        "rate": float(item.rate or 0.0),







        "location": item.location or "",







        "updated_at": item.updated_at,







    }























@router.get("/stock-status")







def get_stock_status_list(







    user: User = Depends(require_super_admin),







    db: Session = Depends(get_db),







):







    items = db.query(StorePurchaseItem).order_by(StorePurchaseItem.name, StorePurchaseItem.stock_item).all()







    return [_stock_status_item_dict(item) for item in items]























@router.put("/stock-status/batch")







def batch_update_stock_limits(







    payload: BatchStockLimitPayload,







    user: User = Depends(require_super_admin),







    db: Session = Depends(get_db),







):







    updated_items = []







    for entry in payload.items:







        item = db.get(StorePurchaseItem, entry.item_id)







        if not item:







            continue







        if entry.out_of_stock_limit is not None:







            item.out_of_stock_limit = entry.out_of_stock_limit







        if entry.low_stock_limit is not None:







            item.reorder_level = entry.low_stock_limit







        item.status = _calculate_status(item.quantity or 0.0, item.out_of_stock_limit, item.reorder_level)







        updated_items.append(item)















    db.commit()







    for item in updated_items:







        db.refresh(item)















    log_activity(







        db,







        "Batch Stock Limits Updated",







        "StoreInventory",







        f"Super Admin updated Out of Stock Limits for {len(updated_items)} items.",







        user.name,







        workspace="Store",







    )







    return [_stock_status_item_dict(item) for item in updated_items]























@router.put("/stock-status/{item_id}")







def update_stock_status_limit(







    item_id: int,







    payload: StockLimitPayload,







    user: User = Depends(require_super_admin),







    db: Session = Depends(get_db),







):







    item = db.get(StorePurchaseItem, item_id)







    if not item:







        raise HTTPException(status_code=404, detail="Inventory item not found.")















    old_limit = getattr(item, "out_of_stock_limit", 0.0) or 0.0







    old_low = getattr(item, "reorder_level", 0.0) or 0.0







    if payload.out_of_stock_limit is not None:







        item.out_of_stock_limit = payload.out_of_stock_limit







    if payload.low_stock_limit is not None:







        item.reorder_level = payload.low_stock_limit







    item.status = _calculate_status(item.quantity or 0.0, item.out_of_stock_limit, item.reorder_level)







    db.commit()







    db.refresh(item)















    log_activity(







        db,







        "Stock Limit Updated",







        "StoreInventory",







        f"Super Admin updated limits for '{item.name}' ({item.stock_item or 'General'}): Out of Stock Limit={item.out_of_stock_limit}, Low Stock Limit={item.reorder_level}. Status recalculated to '{item.status}'.",







        user.name,







        entity_id=item.id,







        entity_name=item.name,







        workspace="Store",







    )







    return _stock_status_item_dict(item)















@router.get("/purchase-orders")







def orders(_: User = Depends(store_user), db: Session = Depends(get_db)):







    # "Draft" is no longer a Store PO status.  Keep legacy records usable







    # rather than exposing a retired status in the Purchase Order list.







    if db.query(StorePurchaseOrder).filter(StorePurchaseOrder.status == "Draft").update({"status": "Pending Approval"}, synchronize_session=False):







        db.commit()







    records = db.query(StorePurchaseOrder).filter(StorePurchaseOrder.is_deleted == False).order_by(StorePurchaseOrder.created_at.desc()).all()







    return [_order_response(record, db=db) for record in records]























@router.get("/purchase-orders/last-po-price")



def get_last_po_price(



    item_description: str = Query(..., description="Item description to find last PO price for"),



    exclude_order_id: Optional[int] = Query(None, description="Optional PO ID to exclude"),



    _: User = Depends(store_user),



    db: Session = Depends(get_db)



):



    clean_desc = (item_description or "").strip()



    if not clean_desc:



        return {"price": None}







    target_norm = " ".join(clean_desc.lower().replace(",", ", ").split())







    query = (



        db.query(StorePurchaseOrderLine, StorePurchaseOrder)



        .join(StorePurchaseOrder, StorePurchaseOrderLine.order_id == StorePurchaseOrder.id)



        .filter(



            StorePurchaseOrder.is_deleted == False,



            StorePurchaseOrderLine.unit_rate > 0



        )



    )



    if exclude_order_id:



        query = query.filter(StorePurchaseOrder.id != exclude_order_id)







    query = query.order_by(StorePurchaseOrder.created_at.desc(), StorePurchaseOrder.id.desc())







    all_lines = query.all()



    for line, order in all_lines:



        line_norm = " ".join((line.item_description or "").lower().replace(",", ", ").split())



        if line_norm == target_norm:



            return {"price": float(line.unit_rate), "po_number": order.order_number, "order_id": order.id}







    return {"price": None}











@router.get("/purchase-orders/{order_id}")







def get_order(order_id: int, _: User = Depends(store_user), db: Session = Depends(get_db)):







    order = db.get(StorePurchaseOrder, order_id)







    if not order or order.is_deleted:







        raise HTTPException(status_code=404, detail="Purchase order not found.")







    if order.status == "Draft":







        order.status = "Pending Approval"







        db.commit()







        db.refresh(order)







    return _order_response(order, db=db)























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







    if order.status not in {"Approved", "Final Approved", "Finally Approved"}:







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







    # Reverse inventory postings linked to this receipt before deleting

    if receipt_line_ids:

        from datetime import date as _date

        txs = db.query(StorePurchaseStockTransaction).filter(

            StorePurchaseStockTransaction.receipt_line_id.in_(receipt_line_ids)

        ).all()



        for tx in txs:
            # Reverse on StorePurchaseItem
            item = db.get(StorePurchaseItem, tx.item_id)
            if item:
                qty = float(tx.quantity or 0)
                item.quantity = max(0.0, float(item.quantity or 0) - qty)
                item.total_received_quantity = max(0.0, float(item.total_received_quantity or 0) - qty)
                if tx.transaction_date:
                    td = tx.transaction_date if isinstance(tx.transaction_date, _date) else tx.transaction_date.date()
                    today = _date.today()
                    if td.year == today.year and td.month == today.month:
                        item.current_month_quantity = max(0.0, float(item.current_month_quantity or 0) - qty)
                item.status = _calculate_status(
                    item.quantity,
                    getattr(item, 'out_of_stock_limit', 0.0),
                    getattr(item, 'reorder_level', 0.0)
                )
                # If item has 0 quantity, reset rate and clear deleted PO reference
                if item.quantity <= 0:
                    item.rate = 0.0
                    if item.reference_no and tx.reference_no and item.reference_no == tx.reference_no:
                        item.reference_no = None
                        item.vendor_name = None
                else:
                    # If remaining quantity > 0, revert rate to previous receipt
                    rem_tx = db.query(StorePurchaseStockTransaction).filter(
                        StorePurchaseStockTransaction.item_id == item.id,
                        StorePurchaseStockTransaction.id != tx.id,
                        StorePurchaseStockTransaction.transaction_type == 'RECEIPT'
                    ).order_by(StorePurchaseStockTransaction.id.desc()).first()
                    if rem_tx:
                        item.rate = rem_tx.rate or 0.0
                        item.vendor_name = rem_tx.vendor_name
                        item.reference_no = rem_tx.reference_no

            # Reverse on StorePurchaseStockItem (find by material_id)
            stock_items = db.query(StorePurchaseStockItem).filter(
                StorePurchaseStockItem.material_id == tx.item_id
            ).all()

            if len(stock_items) == 1:
                si = stock_items[0]
                si.total_received_quantity = max(0.0, float(si.total_received_quantity or 0) - float(tx.quantity or 0))
                si.available_quantity = max(0.0, float(si.available_quantity or 0) - float(tx.quantity or 0))
                if (si.available_quantity or 0) <= 0:
                    si.rate = 0.0
                    si.vendor_name = None
            elif len(stock_items) > 1:
                matched_line = next((ln for ln in receipt.lines if ln.id == tx.receipt_line_id), None)
                if matched_line:
                    norm_name = (matched_line.item_description or '').strip().lower()
                    for si in stock_items:
                        if (si.name or '').strip().lower() == norm_name:
                            si.total_received_quantity = max(0.0, float(si.total_received_quantity or 0) - float(tx.quantity or 0))
                            si.available_quantity = max(0.0, float(si.available_quantity or 0) - float(tx.quantity or 0))
                            if (si.available_quantity or 0) <= 0:
                                si.rate = 0.0
                                si.vendor_name = None
                            break

            tx.receipt_line_id = None
            db.flush()
            db.delete(tx)

        db.flush()

    # Delete only the material receipt; retain the Purchase Order intact
    db.delete(receipt)
    db.commit()
    return {"message": "Material receipt deleted successfully."}















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



        primary = level_group[0]



        _, first_user_id, _, final_user_id = _stage_signature_configs(level_group)



        approver_ids = {link.user_id for level in level_group for link in level.approvers}



        if not final_user_id:



            raise HTTPException(status_code=422, detail=f"Configure a Final Approved By user for amount-wise approval level '{primary.name}' before submitting this PO.")



        if not first_user_id or first_user_id == final_user_id:



            raise HTTPException(status_code=422, detail=f"Configure a distinct Approved By user for amount-wise approval level '{primary.name}'.")







    for level_group in applicable_levels:







        level = level_group[0]







        approvers = [link for configured_level in level_group for link in configured_level.approvers]







        if not approvers:







            raise HTTPException(status_code=422, detail=f"Approval level '{level.name}' has no approvers.")







        _, first_user_id, _, final_user_id = _stage_signature_configs(level_group)



        if not first_user_id or first_user_id == final_user_id:



            raise HTTPException(status_code=422, detail=f"Approved By and Final Approved By must be different users for '{level.name}'.")



        if not final_user_id:



            raise HTTPException(status_code=422, detail=f"Configure a Final Approved By user for approval level '{level.name}' before resubmitting this PO.")



        if not first_user_id and {link.user_id for configured_level in level_group for link in configured_level.approvers} <= {final_user_id}:



            raise HTTPException(status_code=422, detail=f"Assign a separate Approved By user for '{level.name}'. The Final Approved By user cannot approve the first stage.")







    order = StorePurchaseOrder(







        order_number=payload.order_number.strip(), supplier=payload.supplier.strip(),







        item_name=first_line.item_description, quantity=sum(line.quantity for line in payload.items),







        unit_price=first_line.unit_rate, status="Pending Approval" if applicable_levels else "Finally Approved", po_type=payload.po_type, created_by=user.name,



        created_by_user_id=user.id, approval_cycle=1,







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







    _assign_first_approval_stages(order, applicable_levels, db)







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







    if applicable_levels:



        first_approvers = {a.approver_id for a in order.approvals



                           if a.approval_cycle == order.approval_cycle and a.workflow_role in {"checked_by", "approver"}}



        for approver_id in first_approvers:



            _notify_po_user(approver_id, "New Purchase Order Pending Approval",



                            f"PO {order.order_number} submitted by {user.name} is awaiting your approval.", order, db=db)







    log_activity(db, "Store PO Created", "StorePurchaseOrder", f"Created Store PO {order.order_number} for {order.supplier}.", user.name, entity_id=order.id, entity_name=order.order_number, workspace="Store")







    return _order_response(order, db=db)















@router.delete("/purchase-orders/{order_id}")







def delete_order(order_id: int, user: User = Depends(store_user), db: Session = Depends(get_db)):







    """Soft delete the PO without changing independent Items/Stock records."""







    order = db.get(StorePurchaseOrder, order_id)







    if not order or order.is_deleted: raise HTTPException(status_code=404, detail="Purchase Order not found.")







    is_creator = is_order_prepared_by(order, user, db)



    is_admin = application_role(user, db, "store_purchase") == "admin" or user.is_super_admin



    if not (is_creator or is_admin):



        raise HTTPException(status_code=403, detail="Only the Prepared By user or Store Admin can delete this Purchase Order.")







    order_number = order.order_number







    now = datetime.utcnow()







    order.is_deleted = True







    order.deleted_at = now







    order.deleted_by = user.name or "Store Admin"







    order.permanent_delete_at = now + timedelta(hours=24)







    db.commit()







    log_activity(db, "Store PO Deleted", "StorePurchaseOrder", f"Soft-deleted Store PO {order_number}.", user.name, entity_name=order_number, workspace="Store")







    return {"message": "Purchase Order moved to Recently Deleted."}















@router.put("/purchase-orders/{order_id}")







def update_order(order_id: int, payload: OrderPayload, user: User = Depends(store_user), db: Session = Depends(get_db)):







    """Update Purchase Order independently from Items/Stock."""







    order = db.get(StorePurchaseOrder, order_id)







    if not order or order.is_deleted: raise HTTPException(status_code=404, detail="Purchase Order not found.")







    rejected = order.status in {"Rejected by Approved By", "Rejected by Final Approved By", "Rejected"}



    is_resubmitting = rejected or order.status == "Pending Approval"



    is_creator = is_order_prepared_by(order, user, db)

    is_admin = application_role(user, db, "store_purchase") == "admin" or user.is_super_admin



    if rejected or order.status == "Pending Approval":

        if not (is_creator or is_admin):

            raise HTTPException(status_code=403, detail="Only the Prepared By user or Store admin can edit this Purchase Order.")

    else:

        if order.status == "Partially Approved":

            raise HTTPException(status_code=409, detail="A Purchase Order cannot be edited while stage 2 approval is in progress.")

        if not is_admin:

            raise HTTPException(status_code=403, detail="Store admin access is required to edit this Purchase Order.")







    current = list(order.line_items)







    if payload.order_number.strip() != order.order_number and db.query(StorePurchaseOrder).filter(StorePurchaseOrder.order_number == payload.order_number.strip(), StorePurchaseOrder.id != order_id).first():







        raise HTTPException(status_code=409, detail="Order number already exists.")







    subtotal = round(sum(line.quantity * line.unit_rate for line in payload.items), 2)







    gst_amount = round(subtotal * payload.gst_rate / 100, 2)







    if order.status in {"Approved", "Final Approved", "Finally Approved", "Completed", "Partially Received"}:







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







    if is_resubmitting:



        order.approval_cycle = (order.approval_cycle or 1) + 1



        applicable_levels = _active_levels_for_amount(db, detail.grand_total)



        for level_group in applicable_levels:



            if not any(level.approvers for level in level_group):



                raise HTTPException(status_code=422, detail=f"Approval level '{level_group[0].name}' has no approvers.")



            if not level_group[0].approved_by_user_id:



                raise HTTPException(status_code=422, detail=f"Configure a Final Approved By user for approval level '{level_group[0].name}' before resubmitting this PO.")



        order.status = "Pending Approval" if applicable_levels else "Finally Approved"



        _assign_first_approval_stages(order, applicable_levels, db)







    db.add(StorePurchaseOrderActivity(







        order_id=order.id,







        action="Resubmitted" if is_resubmitting else "Updated",







        details="Rejected Purchase Order edited and resubmitted for approval." if is_resubmitting else "Purchase Order details updated.",







        performed_by=user.name,







    ))







    db.commit(); db.refresh(order)







    if is_resubmitting:



        first_approvers = db.query(StorePurchaseOrderApproval).filter_by(



            order_id=order.id, approval_cycle=order.approval_cycle).all()



        for approval in first_approvers:



            if approval.workflow_role in {"checked_by", "approver"}:



                _notify_po_user(approval.approver_id, "Purchase Order Resubmitted",



                    f"PO {order.order_number} was edited by Prepared By and resubmitted for your approval.", order, db=db)







    log_activity(db, "Store PO Resubmitted" if is_resubmitting else "Store PO Updated", "StorePurchaseOrder", f"{'Resubmitted' if is_resubmitting else 'Updated'} Store PO {order.order_number}.", user.name, entity_id=order.id, entity_name=order.order_number, workspace="Store")







    return _order_response(order, db=db)















@router.post("/purchase-orders/{order_id}/complete")







def complete_order(order_id: int, user: User = Depends(store_admin), db: Session = Depends(get_db)):







    """Close the PO after goods are received. This changes only the PO







    workflow status; Items/Stock remain an independent manual workflow."""







    order = db.get(StorePurchaseOrder, order_id)







    if not order or order.is_deleted: raise HTTPException(status_code=404, detail="Purchase Order not found.")







    if order.status not in {"Finally Approved", "Final Approved", "Approved", "Partially Received"}:



        raise HTTPException(status_code=409, detail="Only a finally approved Purchase Order can be completed.")







    order.status = "Completed"







    db.add(StorePurchaseOrderActivity(







        order_id=order.id,







        action="Completed",







        details="Purchase Order marked as completed.",







        performed_by=user.name,







    ))







    db.commit(); db.refresh(order)







    log_activity(db, "Store PO Completed", "StorePurchaseOrder", f"Store PO {order.order_number} marked as completed.", user.name, entity_id=order.id, entity_name=order.order_number, workspace="Store")







    return _order_response(order, db=db)















@router.get("/reports")







def reports(_: User = Depends(store_user), db: Session = Depends(get_db)):







    return {"orders": db.query(StorePurchaseOrder).count(), "inventory": db.query(StorePurchaseItem).count()}



















def _build_signature_config(order: StorePurchaseOrder, db):



    """Build the signature_config dict for a PO response.



    Determines the applicable approval level slab from grand_total,



    then checks approval history to decide which signatures to show.



    Returns a safe empty config on any DB error (e.g. tables not migrated yet)."""



    try:



        return _build_signature_config_inner(order, db)



    except Exception:
        return {



            "prepared_by_list": [],



            "checked_by":  {"user_id": None, "user_name": None, "signature_url": None, "approved": False, "action_at": None},



            "approved_by": {"user_id": None, "user_name": None, "signature_url": None, "approved": False, "action_at": None},



        }











def _build_signature_config_inner(order: StorePurchaseOrder, db):



    """Inner implementation — called by _build_signature_config with error guard."""



    from app.models.models import POPreparedByConfig



    detail = order.details



    grand_total = detail.grand_total if detail else 0







    # Prepared By — only the entry matching the PO creator



    # PO.created_by stores the user's name (string). Match against user_name in config.



    creator_name = (order.created_by or "").strip().lower()



    pb_entries = db.query(POPreparedBySignature).order_by(POPreparedBySignature.display_order, POPreparedBySignature.id).all() if db else []



    # Find entry whose user_name matches the PO creator



    matched = [e for e in pb_entries if (e.user_name or "").strip().lower() == creator_name]



    # If no match found, fall back to all entries (graceful degradation)



    if not matched:



        matched = pb_entries



    prepared_by_list = [



        {



            "id": e.id,



            "user_id": e.user_id,



            "user_name": e.user_name,



            "signature_url": e.signature_url,



        }



        for e in matched



    ]







    # Resolve signatory settings across every legacy row merged into the same slab.



    matching_level_groups = _active_levels_for_amount(db, grand_total)



    applicable_group = matching_level_groups[-1] if matching_level_groups else []



    first_config, configured_first_id, final_config, configured_final_id = _stage_signature_configs(applicable_group) if applicable_group else (None, None, None, None)







    cycle_approvals = [a for a in order.approvals if a.approval_cycle == order.approval_cycle]



    cb_approval = next((a for a in cycle_approvals if a.workflow_role == "checked_by" and a.action == "Approved"), None)



    final_status_reached = order.status in {"Finally Approved", "Completed"}



    ab_approval = next((a for a in cycle_approvals if final_status_reached and a.workflow_role == "approved_by" and a.action == "Approved"), None)



    cb_approved = cb_approval is not None



    ab_approved = ab_approval is not None



    checked_by_name = (cb_approval.approver_name or (cb_approval.approver.name if cb_approval.approver else None)) if cb_approval else (first_config.checked_by_user.name if first_config and first_config.checked_by_user else None)



    approved_by_name = (ab_approval.approver_name or (ab_approval.approver.name if ab_approval.approver else None)) if ab_approval else (final_config.approved_by_user.name if final_config and final_config.approved_by_user else None)







    return {



        "prepared_by_list": prepared_by_list,



        "checked_by": {



            "user_id": cb_approval.approver_id if cb_approval else configured_first_id,



            "user_name": checked_by_name,



            "signature_url": cb_approval.signature_url if cb_approval else None,



            "approved": cb_approved,



            "action_at": cb_approval.action_at if cb_approval else None,



            "remarks": cb_approval.remarks if cb_approval else None,



        },



        "approved_by": {



            "user_id": ab_approval.approver_id if ab_approval else configured_final_id,



            "user_name": approved_by_name,



            "signature_url": ab_approval.signature_url if ab_approval else None,



            "approved": ab_approved,



            "action_at": ab_approval.action_at if ab_approval else None,



            "remarks": ab_approval.remarks if ab_approval else None,



        },



    }



















def _order_response(order: StorePurchaseOrder, db=None):







    session = object_session(order)







    detail = order.details







    received_quantity = sum(







        float(line.received_quantity or 0)







        for receipt in order.material_receipts







        for line in receipt.lines







    )







    return {







        "id": order.id, "order_number": order.order_number, "supplier": order.supplier,







        "status": order.status, "po_type": order.po_type, "created_at": order.created_at, "created_by": order.created_by,



        "created_by_user_id": order.created_by_user_id, "approval_cycle": order.approval_cycle,







        "received_quantity": received_quantity,







        "activities": [{







            "id": activity.id, "action": activity.action, "details": activity.details,







            "performed_by": activity.performed_by, "created_at": activity.created_at,







        } for activity in order.activities],







        "approval_history": [{"id": approval.id, "level_id": approval.level_id, "level": approval.level_name,







            "level_order": approval.level_order, "approval_cycle": approval.approval_cycle,



            "rule": approval.approval_rule, "approver_id": approval.approver_id,







            "approver_name": approval.approver_name or (approval.approver.name if approval.approver else None), "action": approval.action, "action_at": approval.action_at,







            "rejection_reason": approval.rejection_reason, "remarks": approval.remarks,



            "workflow_role": approval.workflow_role, "signature_url": approval.signature_url} for approval in order.approvals



            if not (approval.action == "Pending" and _is_final_user_misassigned_to_first_stage(approval, db or session))],







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







        "remark_1": detail.remark_1 if detail else None,







        "remark_2": detail.remark_2 if detail else None,







        "items": [{"id": line.id, "line_number": line.line_number, "item_description": line.item_description,



                    "item_name": (_inventory_group_and_item(session, line.item_description)[1] if session and _inventory_group_and_item(session, line.item_description)[1] != "General" else line.item_description),



                    "group_name": (_inventory_group_and_item(session, line.item_description)[0] if session else None),



                    "quantity": line.quantity, "uom": line.uom, "unit_rate": line.unit_rate, "amount": line.amount}



                   for line in order.line_items],



        "signature_config": _build_signature_config(order, db or session),
        "material_receipts": [_material_receipt_response(r) for r in sorted(order.material_receipts or [], key=lambda x: str(x.invoice_date or x.created_at or ""), reverse=True)],



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







    uoms_supplied: list[str] | str | None = None







    status: str = "Active"























def _parse_json_list(raw):



    """Parse a JSON-encoded list or comma-separated string from DB into a Python list."""



    if not raw:



        return []



    try:



        result = __import__('json').loads(raw)



        return result if isinstance(result, list) else [str(result)]



    except Exception:



        return [x.strip() for x in raw.split(",") if x.strip()]











def _serialize_vendor(v: StoreVendor):







    items = _parse_json_list(v.items_supplied)







    return {







        "id": v.id,







        "vendor_name": v.vendor_name,







        "person_name": v.person_name,







        "vendor_gst": v.vendor_gst,







        "contact": v.contact,







        "address": v.address,







        "items_supplied": items,







        "uoms_supplied": _parse_json_list(v.uoms_supplied),







        "status": v.status or "Active",







        "created_at": v.created_at.isoformat() if v.created_at else None,







        "updated_at": v.updated_at.isoformat() if v.updated_at else None,







    }























# ─────────────────────────────────────────────────────────────



# PO SIGNATURE & APPROVAL CONFIGURATION  (Super Admin only)



# ─────────────────────────────────────────────────────────────







def _sig_url(url: str | None) -> str | None:



    """Return a full URL for static file access if stored as relative path."""



    return url  # stored as full URL / relative path served by /static mount







def _prepared_by_response(cfg):



    if not cfg:



        return {"user_id": None, "user_name": None, "signature_url": None}



    return {



        "user_id": cfg.user_id,



        "user_name": cfg.user_name,



        "signature_url": _sig_url(cfg.signature_url),



    }







def _level_sig_response(level: POApprovalLevel):



    return {



        "level_id": level.id,



        "level_name": level.name,



        "min_amount": level.min_amount,



        "max_amount": level.max_amount,



        "checked_by_user_id": level.checked_by_user_id,



        "checked_by_user_name": level.checked_by_user.name if level.checked_by_user else None,



        "checked_by_signature_url": _sig_url(level.checked_by_signature_url),



        "approved_by_user_id": level.approved_by_user_id,



        "approved_by_user_name": level.approved_by_user.name if level.approved_by_user else None,



        "approved_by_signature_url": _sig_url(level.approved_by_signature_url),



    }











def _prepared_by_list_response(entries):



    return [



        {



            "id": e.id,



            "user_id": e.user_id,



            "user_name": e.user_name,



            "signature_url": _sig_url(e.signature_url),



            "display_order": e.display_order,



        }



        for e in entries



    ]











@router.get("/po-signature-config")



def get_signature_config(user: User = Depends(require_super_admin), db: Session = Depends(get_db)):



    """Fetch full PO signature configuration: prepared-by list + all slab level sig configs."""



    prepared_by_list = db.query(POPreparedBySignature).order_by(POPreparedBySignature.display_order, POPreparedBySignature.id).all()



    levels = db.query(POApprovalLevel).filter(POApprovalLevel.is_enabled == True).order_by(POApprovalLevel.min_amount).all()



    users = db.query(User).filter(User.is_active == True).order_by(User.name).all()



    return {



        "prepared_by_list": _prepared_by_list_response(prepared_by_list),



        "levels": [_level_sig_response(lv) for lv in levels],



        "users": [{"id": u.id, "name": u.name, "email": u.email} for u in users],



    }











class PreparedByPayload(BaseModel):



    user_id: int



    display_order: int = 0







@router.post("/po-signature-config/prepared-by/add")



def add_prepared_by(payload: PreparedByPayload, user: User = Depends(require_super_admin), db: Session = Depends(get_db)):



    """Add a new Prepared By person (or update if already added)."""



    target = db.get(User, payload.user_id)



    if not target:



        raise HTTPException(status_code=404, detail="User not found.")



    existing = db.query(POPreparedBySignature).filter(POPreparedBySignature.user_id == payload.user_id).first()



    if existing:



        existing.user_name = target.name



        existing.display_order = payload.display_order



        existing.added_by = user.name



        db.commit(); db.refresh(existing)



        return _prepared_by_list_response([existing])[0]



    entry = POPreparedBySignature(



        user_id=target.id, user_name=target.name,



        display_order=payload.display_order, added_by=user.name



    )



    db.add(entry); db.commit(); db.refresh(entry)



    return _prepared_by_list_response([entry])[0]











@router.delete("/po-signature-config/prepared-by/{entry_id}")



def remove_prepared_by(entry_id: int, user: User = Depends(require_super_admin), db: Session = Depends(get_db)):



    """Remove a Prepared By person by entry id."""



    entry = db.get(POPreparedBySignature, entry_id)



    if not entry:



        raise HTTPException(status_code=404, detail="Entry not found.")



    db.delete(entry); db.commit()



    return {"message": "Removed."}











@router.post("/po-signature-config/prepared-by/{entry_id}/signature")



async def upload_prepared_by_signature(entry_id: int, file: UploadFile = File(...), user: User = Depends(require_super_admin), db: Session = Depends(get_db)):



    """Upload signature image for a specific Prepared By person."""



    entry = db.get(POPreparedBySignature, entry_id)



    if not entry:



        raise HTTPException(status_code=404, detail="Prepared By entry not found.")



    content_bytes = await read_upload_bytes(file)



    file_url = save_upload_bytes(content_bytes, file.filename, "po-signatures")



    entry.signature_url = file_url



    db.commit(); db.refresh(entry)



    return {"signature_url": file_url, "entry_id": entry_id}











class SlabUserPayload(BaseModel):



    user_id: int







@router.put("/po-signature-config/levels/{level_id}/checked-by")



def update_level_checked_by(level_id: int, payload: SlabUserPayload, user: User = Depends(require_super_admin), db: Session = Depends(get_db)):



    """Set the Approved By user for a specific approval level slab."""



    level = db.get(POApprovalLevel, level_id)



    if not level:



        raise HTTPException(status_code=404, detail="Approval level not found.")



    target = db.get(User, payload.user_id)



    if not target:



        raise HTTPException(status_code=404, detail="User not found.")



    level.checked_by_user_id = target.id



    db.commit(); db.refresh(level)



    return _level_sig_response(level)











@router.put("/po-signature-config/levels/{level_id}/approved-by")



def update_level_approved_by(level_id: int, payload: SlabUserPayload, user: User = Depends(require_super_admin), db: Session = Depends(get_db)):



    """Set the Final Approved By user for a specific approval level slab."""



    level = db.get(POApprovalLevel, level_id)



    if not level:



        raise HTTPException(status_code=404, detail="Approval level not found.")



    target = db.get(User, payload.user_id)



    if not target:



        raise HTTPException(status_code=404, detail="User not found.")



    level.approved_by_user_id = target.id



    db.commit(); db.refresh(level)



    return _level_sig_response(level)











@router.post("/po-signature-config/levels/{level_id}/signature")



async def upload_level_signature(level_id: int, sig_type: str, file: UploadFile = File(...), user: User = Depends(require_super_admin), db: Session = Depends(get_db)):



    """Upload signature for checked_by or approved_by on a specific level. sig_type = checked_by | approved_by"""



    if sig_type not in {"checked_by", "approved_by"}:



        raise HTTPException(status_code=422, detail="sig_type must be 'checked_by' or 'approved_by'.")



    level = db.get(POApprovalLevel, level_id)



    if not level:



        raise HTTPException(status_code=404, detail="Approval level not found.")



    content_bytes = await read_upload_bytes(file)



    file_url = save_upload_bytes(content_bytes, file.filename, "po-signatures")



    if sig_type == "checked_by":



        level.checked_by_signature_url = file_url



    else:



        level.approved_by_signature_url = file_url



    db.commit(); db.refresh(level)



    return {"signature_url": file_url, "sig_type": sig_type}















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







        uoms_supplied=json.dumps(payload.uoms_supplied) if isinstance(payload.uoms_supplied, list) else (payload.uoms_supplied or None),







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







    if payload.uoms_supplied is not None:







        vendor.uoms_supplied = json.dumps(payload.uoms_supplied) if isinstance(payload.uoms_supplied, list) else str(payload.uoms_supplied).strip()







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































