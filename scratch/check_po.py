from app.database import SessionLocal
from app.models.models import PurchaseOrder, POLineItem, SaleItem
import sys

db = SessionLocal()
po = db.query(PurchaseOrder).filter(PurchaseOrder.po_number == '5500010420').first()
if not po:
    print('PO not found')
    sys.exit(0)

print(f'PO {po.po_number}: total_qty={po.total_qty}, delivered={po.delivered_quantity}')
for li in po.line_items:
    print(f' LI {li.id}: {li.item} - qty={li.quantity}, delivered={li.delivered_quantity}')
    sales = db.query(SaleItem).filter(SaleItem.line_item_id == li.id).all()
    for s in sales:
        print(f'   -> SaleItem {s.id}: qty={s.quantity}')