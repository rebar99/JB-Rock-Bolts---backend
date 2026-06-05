from app.database import SessionLocal
from app.models.models import PurchaseOrder, POLineItem
from sqlalchemy import func, or_

db = SessionLocal()
try:
    # Test the query
    subq = db.query(PurchaseOrder.id).join(PurchaseOrder.line_items).group_by(PurchaseOrder.id).having(func.sum(POLineItem.delivered_quantity) < func.sum(POLineItem.quantity))
    pending_orders = db.query(PurchaseOrder).filter(
        or_(
            PurchaseOrder.delivered_quantity < PurchaseOrder.total_quantity,
            PurchaseOrder.id.in_(subq)
        )
    ).all()
    print(f"Found {len(pending_orders)} pending orders")
    for o in pending_orders:
        print(f"PO: {o.po_number}, Grand Total: {o.grand_total}")
except Exception as e:
    print(f"Error: {e}")
finally:
    db.close()
