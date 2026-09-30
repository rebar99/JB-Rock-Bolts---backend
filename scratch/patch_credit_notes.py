import re

path = r"D:\rebar-jbrocks\JB-Rock-Bolts---backend\app\routers\credit_notes.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Add POLineItem and WOLineItem to imports
content = content.replace(
    "from app.models.models import CreditNote, CreditNoteItem, Sale, WorkOrderSale, SaleItem, WorkOrderSaleItem",
    "from app.models.models import CreditNote, CreditNoteItem, Sale, WorkOrderSale, SaleItem, WorkOrderSaleItem, POLineItem, WOLineItem"
)

# 1. create_credit_note
new_create = """
    taxable_amount = gst_amount = total_amount = 0.0
    for it in payload.items:
        # All quantities in Credit Notes are treated as positive returns/adjustments
        quantity = abs(float(it.credit_qty or 0))
        subtotal = quantity * float(it.unit_price or 0)
        item_gst = subtotal * float(it.gst_rate or 0) / 100
        item_total = subtotal + item_gst
        taxable_amount += subtotal
        gst_amount += item_gst
        total_amount += item_total

        # Adjust PO/WO Line Item Quantity
        if source_sale:
            sale_item = next((si for si in source_sale.items if si.item == it.item), None)
            if sale_item and getattr(sale_item, "line_item_id", None):
                if payload.sale_type.upper() == "PO":
                    po_line = db.query(POLineItem).filter(POLineItem.id == sale_item.line_item_id).first()
                    if po_line:
                        po_line.delivered_quantity -= quantity
                elif payload.sale_type.upper() == "WO":
                    wo_line = db.query(WOLineItem).filter(WOLineItem.id == sale_item.line_item_id).first()
                    if wo_line:
                        wo_line.completed_quantity -= quantity

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
"""
content = re.sub(
    r"    taxable_amount = gst_amount = total_amount = 0\.0\n    for it in payload\.items:.*?    cn\.total_amount = total_amount",
    new_create.strip("\n"),
    content,
    flags=re.DOTALL,
    count=1
)

# 2. update_credit_note
new_update = """
        # Revert old items
        old_items = db.query(CreditNoteItem).filter(CreditNoteItem.credit_note_id == cn_id).all()
        source_sale = None
        if cn.sale_type == "PO" and cn.sale_id:
            source_sale = db.query(Sale).filter(Sale.id == cn.sale_id).first()
        elif cn.sale_type == "WO" and cn.wo_sale_id:
            source_sale = db.query(WorkOrderSale).filter(WorkOrderSale.id == cn.wo_sale_id).first()
        
        if source_sale:
            for old_it in old_items:
                old_qty = abs(float(old_it.credit_qty or 0))
                sale_item = next((si for si in source_sale.items if si.item == old_it.item), None)
                if sale_item and getattr(sale_item, "line_item_id", None):
                    if cn.sale_type == "PO":
                        po_line = db.query(POLineItem).filter(POLineItem.id == sale_item.line_item_id).first()
                        if po_line:
                            po_line.delivered_quantity += old_qty
                    elif cn.sale_type == "WO":
                        wo_line = db.query(WOLineItem).filter(WOLineItem.id == sale_item.line_item_id).first()
                        if wo_line:
                            wo_line.completed_quantity += old_qty

        # Replace all items
        db.query(CreditNoteItem).filter(CreditNoteItem.credit_note_id == cn_id).delete()
        taxable_amount = gst_amount = total_amount = 0.0
        for it in payload.items:
            quantity = abs(float(it.credit_qty or 0))
            subtotal = quantity * float(it.unit_price or 0)
            item_gst = subtotal * float(it.gst_rate or 0) / 100
            item_total = subtotal + item_gst
            taxable_amount += subtotal
            gst_amount += item_gst
            total_amount += item_total

            # Apply new items
            if source_sale:
                sale_item = next((si for si in source_sale.items if si.item == it.item), None)
                if sale_item and getattr(sale_item, "line_item_id", None):
                    if cn.sale_type == "PO":
                        po_line = db.query(POLineItem).filter(POLineItem.id == sale_item.line_item_id).first()
                        if po_line:
                            po_line.delivered_quantity -= quantity
                    elif cn.sale_type == "WO":
                        wo_line = db.query(WOLineItem).filter(WOLineItem.id == sale_item.line_item_id).first()
                        if wo_line:
                            wo_line.completed_quantity -= quantity

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
"""
content = re.sub(
    r"        # Replace all items.*?        cn\.taxable_amount = taxable_amount",
    new_update.strip("\n"),
    content,
    flags=re.DOTALL,
    count=1
)

# 3. cancel_credit_note
new_cancel = """
    cn = _load_cn(cn_id, db)
    
    source_sale = None
    if cn.sale_type == "PO" and cn.sale_id:
        source_sale = db.query(Sale).filter(Sale.id == cn.sale_id).first()
    elif cn.sale_type == "WO" and cn.wo_sale_id:
        source_sale = db.query(WorkOrderSale).filter(WorkOrderSale.id == cn.wo_sale_id).first()
    
    if source_sale and cn.status != "Cancelled":
        for old_it in cn.items:
            old_qty = abs(float(old_it.credit_qty or 0))
            sale_item = next((si for si in source_sale.items if si.item == old_it.item), None)
            if sale_item and getattr(sale_item, "line_item_id", None):
                if cn.sale_type == "PO":
                    po_line = db.query(POLineItem).filter(POLineItem.id == sale_item.line_item_id).first()
                    if po_line:
                        po_line.delivered_quantity += old_qty
                elif cn.sale_type == "WO":
                    wo_line = db.query(WOLineItem).filter(WOLineItem.id == sale_item.line_item_id).first()
                    if wo_line:
                        wo_line.completed_quantity += old_qty

    cn.is_deleted = True
"""
content = content.replace("    cn = _load_cn(cn_id, db)\n    cn.is_deleted = True", new_cancel.strip("\n"))

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("Patched credit_notes.py")
