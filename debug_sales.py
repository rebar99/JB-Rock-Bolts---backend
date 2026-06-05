from app.database import SessionLocal
from app.models import models
from sqlalchemy import or_, func

db = SessionLocal()
pos = db.query(models.PurchaseOrder).all()
for po in pos:
    po_num_clean = str(po.po_number or "").strip().lower()
    sales = db.query(models.Sale).filter(
        or_(
            models.Sale.po_id == po.id,
            func.lower(func.trim(models.Sale.po_number)) == po_num_clean
        )
    ).all()
    print(f"PO {po.id} ({po.po_number}): {len(sales)} sales found.")
    for s in sales:
        print(f"  - Sale {s.id}: Status={s.delivery_status}, Challan='{s.delivery_challan_url}'")
db.close()
