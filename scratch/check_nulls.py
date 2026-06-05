
from app.database import SessionLocal
from app.models.models import Sale
import json

db = SessionLocal()
try:
    all_sales = db.query(Sale).all()
    print(f"Total Sales: {len(all_sales)}")
    for s in all_sales:
        print(f"ID: {s.id}, Freight: {s.freight}, GrandTotal: {s.grand_total}")
except Exception as e:
    print(f"ERROR: {e}")
finally:
    db.close()
