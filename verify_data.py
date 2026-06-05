from app.database import SessionLocal
from app.models import models

db = SessionLocal()
pos = db.query(models.PurchaseOrder).all()
print(f"{'PO ID':<10} | {'PO Num':<20} | {'Sales Count':<12} | {'Deliv Status':<15}")
print("-" * 70)
for po in pos:
    sales = db.query(models.Sale).filter(
        (models.Sale.po_id == po.id) | (models.Sale.po_number == po.po_number)
    ).all()
    print(f"{po.id:<10} | {str(po.po_number):<20} | {len(sales):<12} | {po.delivery_status:<15}")
    for s in sales:
        has_challan = bool(s.delivery_challan_url and s.delivery_challan_url.strip())
        print(f"  -> Sale ID: {s.id:<5} | Challan: {has_challan:<6} | Status: {s.delivery_status}")
db.close()
