from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List, Dict


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
    invoice_number: Optional[str] = None
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


# ── Overview Report (Reports → Overview) ──────────────────────────────────────

class OverviewSizeBreakdown(BaseModel):
    size: str
    size_label: str
    ordered_qty: float
    dispatched_qty: float
    pending_qty: float


class OverviewProductSummary(BaseModel):
    product_type: str
    ordered_qty: float
    dispatched_qty: float
    pending_qty: float
    size_breakdown: List[OverviewSizeBreakdown]


class OverviewSummary(BaseModel):
    total_ordered_qty: float
    total_dispatched_qty: float
    total_pending_qty: float
    total_clients: int
    total_active_pos: int
    total_active_wos: int
    products: List[OverviewProductSummary]


class OverviewRow(BaseModel):
    source: str  # "PO" | "WO"
    order_no: str
    client_name: str
    client_key: str
    project: Optional[str] = None
    product_type: str
    item: str
    uom: str = "Nos"
    ordered_qty: float
    dispatched_qty: float
    pending_qty: float
    size: Optional[str] = None
    size_label: str


class OverviewReportOut(BaseModel):
    generated_at: datetime
    summary: OverviewSummary
    rows: List[OverviewRow]
    # source ("PO" | "WO") -> client_key -> deduped project names
    client_projects: Dict[str, Dict[str, List[str]]] = {}


# ── Product-wise Pending Analysis schemas ────────────────────────────────────

class POPendingDetail(BaseModel):
    po_number: str
    po_date: Optional[str] = None
    project_name: Optional[str] = None
    ordered_qty: float
    dispatched_qty: float
    pending_qty: float
    rate: float
    pending_value: float

class ClientPendingDetail(BaseModel):
    client_name: str
    client_key: str
    total_ordered_qty: float
    total_dispatched_qty: float
    pending_qty: float
    pending_value: float
    pos: List[POPendingDetail]

class ProductPendingRow(BaseModel):
    product_label: str
    category: str
    total_ordered_qty: float
    total_dispatched_qty: float
    pending_qty: float
    pending_value: float
    client_count: int
    clients: List[ClientPendingDetail]

class ProductPendingSummary(BaseModel):
    total_pending_qty: float
    total_pending_value: float
    total_products: int
    total_clients: int

class ProductPendingOut(BaseModel):
    summary: ProductPendingSummary
    products: List[ProductPendingRow]
    client_names: List[str]
    product_labels: List[str]
    client_projects: Dict[str, List[str]] = {}

