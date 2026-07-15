from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


class ReportFilter(BaseModel):
    from_date: Optional[datetime] = None
    to_date: Optional[datetime] = None
    product: Optional[str] = None
    client: Optional[str] = None


class ReportRow(BaseModel):
    id: int
    date: str
    client_name: str
    product: str
    location: Optional[str]
    po_number: Optional[str]
    invoice_number: Optional[str]
    e_way_bill_no: Optional[str] = None
    price: float
    subtotal: float = 0
    gst_amount: float = 0
    payment_status: str
    delivery_status: str
    payment_note: Optional[str] = None
    dispatched_qty: float = 0
    total_qty: float = 0
    pending_qty: float = 0
    uom: str = "Nos"


class ReportOut(BaseModel):
    rows: List[ReportRow]
    total_revenue: float
    record_count: int
    avg_order_value: float


class FulfillmentReportRow(BaseModel):
    id: int
    po_number: str
    date: str
    client_name: str
    project: str
    item: str
    total_required: float
    delivered: float
    pending: float
    uom: str
    remark: Optional[str] = None


class FulfillmentReportOut(BaseModel):
    rows: List[FulfillmentReportRow]


class PendingPORow(BaseModel):
    id: int
    po_number: str
    client_name: str
    project: str
    item: str
    subtotal: float
    gst_amount: float
    total_value: float
    pending_subtotal: float
    pending_gst: float
    pending_total: float
    delivered_payment: float = 0
    status: str
    date: str
    uom: str = "Nos"
    total_qty: float = 0
    delivered_qty: float = 0
    pending_qty: float = 0
    remark: Optional[str] = None


class PendingPOReportOut(BaseModel):
    rows: List[PendingPORow]
    total_subtotal: float
    total_gst: float
    total_value: float
    total_pending_value: float
    total_delivered_payment: float = 0
    count: int


class DispatchHistoryRow(BaseModel):
    date: str
    invoice_number: Optional[str] = None
    item: Optional[str] = None
    dispatch_qty: float
    uom: str
    amount: float
    sale_id: Optional[int] = None
    dispatch_id: Optional[int] = None


class POFulfillmentSummaryOut(BaseModel):
    po_id: int
    po_number: str
    client_name: str
    project: Optional[str] = None
    po_date: Optional[str] = None
    po_quantity: float
    delivered_quantity: float
    pending_quantity: float
    uom: str
    subtotal: float
    gst_amount: float
    grand_total: float
    dispatch_history: List[DispatchHistoryRow]
    total_dispatches: int
    last_dispatch_date: Optional[str] = None
    payment_received: float
    pending_payment: float
    delivery_status: str
