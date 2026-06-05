from pydantic import BaseModel, computed_field
from datetime import datetime
from typing import Optional, List
from app.models.models import PaymentStatus


class SaleActivityCreate(BaseModel):
    action: str
    note: Optional[str] = None
    payment_status: Optional[str] = None
    by: Optional[str] = None


class SaleActivityOut(BaseModel):
    id: int
    sale_id: int
    action: str
    note: Optional[str] = None
    payment_status: Optional[str] = None
    at: datetime
    by: Optional[str] = None

    model_config = {"from_attributes": True}


class SaleItemCreate(BaseModel):
    line_item_id: Optional[int] = None
    item: str
    uom: str = "Nos"
    quantity: float
    unit_price: float
    gst_rate: float = 0
    subtotal: float = 0.0
    gst_amount: float = 0.0
    total_amount: float = 0.0

class SaleItemOut(BaseModel):
    id: int
    line_item_id: Optional[int] = None
    item: str
    uom: str
    quantity: float
    unit_price: float
    gst_rate: float
    subtotal: float
    gst_amount: float
    total_amount: float

    model_config = {"from_attributes": True}

class SaleCreate(BaseModel):
    po_id: int
    po_number: str
    invoice_number: Optional[str] = None
    client_name: str
    project: Optional[str] = None
    items: List[SaleItemCreate]
    subtotal: float
    gst_amount: float
    freight: float = 0
    grand_total: float
    payment_status: PaymentStatus = PaymentStatus.PENDING
    payment_note: Optional[str] = None
    invoice_url: Optional[str] = None
    e_way_bill_url: Optional[str] = None
    dispatch_from: Optional[str] = None
    ship_to: Optional[str] = None
    bill_to: Optional[str] = None
    dispatched_through: Optional[str] = None
    e_way_bill_no: Optional[str] = None
    buyers_order_no: Optional[str] = None
    payment_terms: Optional[str] = None
    hsn_code: Optional[str] = None
    created_by: Optional[str] = None

class SaleUpdate(BaseModel):
    invoice_number: Optional[str] = None
    payment_status: Optional[PaymentStatus] = None
    payment_note: Optional[str] = None
    invoice_url: Optional[str] = None
    e_way_bill_url: Optional[str] = None
    dispatch_from: Optional[str] = None
    ship_to: Optional[str] = None
    bill_to: Optional[str] = None
    dispatched_through: Optional[str] = None
    e_way_bill_no: Optional[str] = None
    buyers_order_no: Optional[str] = None
    payment_terms: Optional[str] = None
    delivery_status: Optional[str] = None
    delivery_challan_url: Optional[str] = None
    hsn_code: Optional[str] = None
    items: Optional[List[SaleItemCreate]] = None
    subtotal: Optional[float] = None
    gst_amount: Optional[float] = None
    freight: Optional[float] = None
    grand_total: Optional[float] = None
    updated_by: Optional[str] = None

class SaleOut(BaseModel):
    id: int
    po_id: int
    po_number: str
    invoice_number: Optional[str] = None
    client_name: str
    project: Optional[str] = None
    subtotal: float
    gst_amount: float
    freight: float
    grand_total: float
    payment_status: PaymentStatus
    payment_note: Optional[str] = None
    created_at: datetime
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    invoice_url: Optional[str] = None
    e_way_bill_url: Optional[str] = None
    dispatch_from: Optional[str] = None
    ship_to: Optional[str] = None
    bill_to: Optional[str] = None
    dispatched_through: Optional[str] = None
    e_way_bill_no: Optional[str] = None
    buyers_order_no: Optional[str] = None
    payment_terms: Optional[str] = None
    delivery_status: str = "Not Delivered"
    delivery_challan_url: Optional[str] = None
    hsn_code: Optional[str] = None
    items: List[SaleItemOut] = []
    activities: List[SaleActivityOut] = []

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def item(self) -> str:
        if self.items:
            return ", ".join(i.item for i in self.items)
        return ""

    @computed_field
    @property
    def dispatched_qty(self) -> float:
        return sum(i.quantity for i in self.items)

    @computed_field
    @property
    def invoice_urls(self) -> List[str]:
        return [url for url in (self.invoice_url or "").split(";") if url]

    @computed_field
    @property
    def e_way_bill_urls(self) -> List[str]:
        return [url for url in (self.e_way_bill_url or "").split(";") if url]

    @computed_field
    @property
    def delivery_challan_urls(self) -> List[str]:
        return [url for url in (self.delivery_challan_url or "").split(";") if url]

    @computed_field
    @property
    def unit_price(self) -> float:
        return self.items[0].unit_price if self.items else 0

    @computed_field
    @property
    def gst_rate(self) -> float:
        return self.items[0].gst_rate if self.items else 0

    @computed_field
    @property
    def uom(self) -> str:
        return self.items[0].uom if self.items else "Nos"
