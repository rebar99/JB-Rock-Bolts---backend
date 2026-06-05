
from app.database import SessionLocal
from app.models.models import PurchaseOrder

db = SessionLocal()
try:
    pos = db.query(PurchaseOrder).all()
    for p in pos:
        print(f"ID: {p.id}, Qty: {p.total_quantity}, Price: {p.unit_price}, Sub: {p.subtotal}")
except Exception as e:
    print(f"ERROR: {e}")
finally:
    db.close()
