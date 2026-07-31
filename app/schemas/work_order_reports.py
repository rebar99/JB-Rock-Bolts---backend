from pydantic import BaseModel
from typing import Optional, List


class WorkOrderReportRow(BaseModel):
    id: int
    wo_number: str
    client_name: str
    project: str
    wo_date: str
    item: str
    total_quantity: float
    completed_quantity: float
    pending_quantity: float
    sales_quantity: float = 0
    status: str
    uom: str = "Nos"
    subtotal: float = 0
    gst_amount: float = 0
    grand_total: float = 0


class WorkOrderReportOut(BaseModel):
    rows: List[WorkOrderReportRow]
    total_work_orders: int
    completed_work_orders: int
    pending_work_orders: int
    sales_work_orders: int


class WorkOrderSaleReportRow(BaseModel):
    id: int
    date: str
    invoice_number: Optional[str] = None
    wo_number: str
    client_name: str
    subtotal: float = 0
    gst_amount: float = 0
    grand_total: float = 0
    payment_status: str


class WorkOrderSaleReportOut(BaseModel):
    rows: List[WorkOrderSaleReportRow]
    total_revenue: float
    record_count: int
    avg_order_value: float
