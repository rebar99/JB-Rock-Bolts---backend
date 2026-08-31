import asyncio
from app.database import get_db
from app.models.models import Sale, SaleItem
from app.routers.sales import update_sale
from app.schemas.sale import SaleUpdate
import datetime

db = next(get_db())
sale = db.query(Sale).first()
if sale:
    print(f"Testing on Sale {sale.id} - {sale.invoice_number}")
    payload = SaleUpdate(
        subtotal=sale.subtotal + 10,
        items=[
            {
                "line_item_id": si.line_item_id,
                "item": si.item,
                "uom": si.uom,
                "quantity": si.quantity + 1,
                "unit_price": si.unit_price,
                "gst_rate": si.gst_rate,
                "subtotal": si.subtotal,
                "gst_amount": si.gst_amount,
                "total_amount": si.total_amount
            }
            for si in sale.items
        ]
    )
    try:
        result = update_sale(sale.id, payload, db=db)
        print("Subtotal after update:", result.subtotal)
        print("Items count:", len(result.items))
        print("First item quantity:", result.items[0].quantity if result.items else None)
    except Exception as e:
        print("Exception:", e)
else:
    print("No Sale found")
