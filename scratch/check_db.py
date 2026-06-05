
from app.database import SessionLocal
from app.models.models import PurchaseOrder

db = SessionLocal()
try:
    pos = db.query(PurchaseOrder).all()
    print(f"Total POs in DB: {len(pos)}")
    for po in pos:
        print(f"ID: {po.id}, PO#: {po.po_number}, Client: {po.client_name}")
finally:
    db.close()
