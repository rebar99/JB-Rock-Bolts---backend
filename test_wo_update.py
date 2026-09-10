import sys
import asyncio
from app.database import get_db
from app.models.models import WorkOrder
from app.routers.work_orders import update_work_order
from app.schemas.work_order import WorkOrderUpdate, WOLineItemCreate
import datetime

db = next(get_db())
wo = db.query(WorkOrder).first()
if wo:
    print(f"Testing on WO {wo.id} - {wo.wo_number}")
    payload = WorkOrderUpdate(
        client_name=wo.client_name,
        wo_number=wo.wo_number,
        project=(wo.project or "") + " test",
        line_items=[
            WOLineItemCreate(
                id=li.id,
                item=li.item,
                quantity=li.quantity + 1,
                uom=li.uom,
                unit_price=li.unit_price,
                gst=li.gst,
                freight=li.freight
            ) for li in wo.line_items
        ]
    )
    try:
        result = update_work_order(wo.id, payload, db=db)
        print("Result:", result)
    except Exception as e:
        print("Exception:", e)
else:
    print("No WO found")
