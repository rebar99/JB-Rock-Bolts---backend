"""Patch store_purchase.py to block PO delete if active material receipts exist."""
import re

filepath = r'd:\rebar-jbrocks\JB-Rock-Bolts---backend\app\routers\store_purchase.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# The block to insert — after the 404 check on delete_order
old = '    if not order or order.is_deleted: raise HTTPException(status_code=404, detail="Purchase Order not found.")\r\n\r\n\r\n\r\n\r\n\r\n\r\n\r\n    is_creator = is_order_prepared_by(order, user, db)'

new = '''    if not order or order.is_deleted: raise HTTPException(status_code=404, detail="Purchase Order not found.")

    # Block delete if active material receipts exist for this PO
    active_receipts = db.query(StorePurchaseMaterialReceipt).filter(
        StorePurchaseMaterialReceipt.order_id == order.id,
        StorePurchaseMaterialReceipt.is_deleted == False
    ).all()
    if active_receipts:
        receipt_labels = ", ".join(
            f"Invoice #{r.invoice_number}" if r.invoice_number else f"Receipt #{r.id}"
            for r in active_receipts
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot delete PO: {len(active_receipts)} material receipt(s) are linked "
                f"({receipt_labels}). Please delete all Materials Received entries first."
            )
        )

    is_creator = is_order_prepared_by(order, user, db)'''

if old in content:
    content = content.replace(old, new, 1)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print("SUCCESS: Patch applied.")
else:
    print("ERROR: Target text not found. Trying with LF...")
    old_lf = old.replace('\r\n', '\n')
    if old_lf in content:
        content = content.replace(old_lf, new.replace('\r\n', '\n'), 1)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print("SUCCESS: Patch applied (LF).")
    else:
        # Debug: show surrounding context
        idx = content.find('def delete_order(')
        print(f"def delete_order found at char {idx}")
        print(repr(content[idx:idx+800]))
