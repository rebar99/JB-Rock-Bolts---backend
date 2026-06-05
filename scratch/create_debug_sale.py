
from app.database import SessionLocal
from app.models.models import Sale, SaleItem, PurchaseOrder
from app.models.models import PaymentStatus

db = SessionLocal()
try:
    po = db.query(PurchaseOrder).first()
    if not po:
        print("ERROR: No PO found to link sale to.")
    else:
        sale = Sale(
            po_id=po.id,
            po_number=po.po_number,
            client_name=po.client_name,
            subtotal=1000.0,
            gst_amount=180.0,
            freight=50.0,
            grand_total=1230.0,
            payment_status=PaymentStatus.PENDING,
            created_by="system_debug"
        )
        db.add(sale)
        db.flush()
        
        item = SaleItem(
            sale_id=sale.id,
            item="Debug Item",
            quantity=1,
            unit_price=1000.0,
            subtotal=1000.0,
            gst_amount=180.0,
            total_amount=1180.0
        )
        db.add(item)
        db.commit()
        print(f"SUCCESS: Created debug sale with ID {sale.id}")
except Exception as e:
    print(f"ERROR: {e}")
    db.rollback()
finally:
    db.close()
