from pydantic import BaseModel
from typing import List, Any, Dict, Optional


class DashboardStats(BaseModel):
    total_revenue: float
    total_orders: int
    total_clients: int
    delivered_orders: int
    pending_payments: int


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
