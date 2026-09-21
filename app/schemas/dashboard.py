from pydantic import BaseModel
from typing import List, Any, Dict, Optional


class DashboardStats(BaseModel):
    total_revenue: float
    po_revenue: float = 0
    wo_revenue: float = 0
    credit_note_adjustment: float = 0
    total_orders: int
    total_clients: int
    delivered_orders: int
    pending_payments: int
    po_invoice_count: int = 0
    wo_invoice_count: int = 0
    po_client_count: int = 0
    wo_client_count: int = 0
    po_order_count: int = 0
    wo_order_count: int = 0
    completed_po_count: int = 0
    pending_po_count: int = 0
    completed_wo_count: int = 0
    pending_wo_count: int = 0


class ChartDataPoint(BaseModel):
    name: str
    value: float


class MonthlyTrend(BaseModel):
    month: str
    revenue: float
    orders: int


class ChartData(BaseModel):
    sales_by_product: List[ChartDataPoint]
    payment_status: List[ChartDataPoint]
    monthly_trend: List[MonthlyTrend]


class RecentSale(BaseModel):
    id: int
    client_name: str
    product: str
    price: float
    payment_status: str
    delivery_status: str
    date: str
    invoice_number: Optional[str] = None
    po_number: Optional[str] = None
    sale_type: str = "PO"
