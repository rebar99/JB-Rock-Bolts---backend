
from app.database import SessionLocal
from app.models.models import Sale
import json

db = SessionLocal()
try:
    sales = db.query(Sale).all()
    print(f"SUCCESS: Found {len(sales)} sales")
    for s in sales:
        print(f"ID: {s.id}, PO: {s.po_number}, Items: {len(s.items)}")
except Exception as e:
    print(f"ERROR: {e}")
finally:
    db.close()
