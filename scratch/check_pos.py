
from app.database import SessionLocal
from app.models.models import PurchaseOrder

db = SessionLocal()
try:
    pos = db.query(PurchaseOrder).all()
    print(f"Total POs: {len(pos)}")
    for p in pos:
        print(f"PO: {p.po_number}, Client: {p.client_name}")
except Exception as e:
    print(f"ERROR: {e}")
finally:
    db.close()
