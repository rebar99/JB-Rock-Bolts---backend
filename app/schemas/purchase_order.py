from pydantic import BaseModel, computed_field
from datetime import datetime
from typing import Optional, List

class POLineItemBase(BaseModel):
    item: str
    quantity: float = 0
    delivered_quantity: float = 0
    uom: str = "Nos"
    unit_price: float = 0
    gst: Optional[str] = "0"
    freight: float = 0

class POLineItemCreate(POLineItemBase):
    id: Optional[int] = None

class POLineItemOut(POLineItemBase):
    id: int
    
    @computed_field
    @property
    def pending_quantity(self) -> float:
        return max(0, self.quantity - self.delivered_quantity)

    @computed_field
    @property
    def subtotal(self) -> float:
        return self.quantity * self.unit_price

    @computed_field
    @property
    def gst_rate(self) -> float:
        if self.gst is None or self.gst.strip() in ("", "0"):
            return 0.0
        if self.gst.startswith("₹"):
            return 0.0
        cleaned = self.gst.replace("%", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    @computed_field
    @property
    def gst_amount(self) -> float:
        if self.gst and self.gst.startswith("₹"):
            cleaned = self.gst.replace("₹", "").replace(",", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                return 0.0
        return self.subtotal * self.gst_rate / 100

    @computed_field
    @property
    def grand_total(self) -> float:
        return self.subtotal + self.gst_amount + self.freight

    model_config = {"from_attributes": True}

class PurchaseOrderBase(BaseModel):
    client_name: str
    po_number: str
    project: Optional[str] = None
    project_id: Optional[int] = None
    location: Optional[str] = None
    gst: Optional[str] = "0"
    freight: float = 0
    payment_terms: Optional[str] = None
    validity_date: Optional[datetime] = None
    file_url: Optional[str] = None

class PurchaseOrderCreate(PurchaseOrderBase):
    created_by: Optional[str] = None
    line_items: List[POLineItemCreate] = []

class PurchaseOrderUpdate(BaseModel):
    client_name: Optional[str] = None
    po_number: Optional[str] = None
    project: Optional[str] = None
    project_id: Optional[int] = None
    location: Optional[str] = None
    gst: Optional[str] = None
    freight: Optional[float] = None
    payment_terms: Optional[str] = None
    validity_date: Optional[datetime] = None
    file_url: Optional[str] = None
    last_updated_by: Optional[str] = None
    line_items: Optional[List[POLineItemCreate]] = None

class PurchaseOrderOut(PurchaseOrderBase):
    id: int
    created_at: datetime
    created_by: Optional[str] = None
    last_opened_at: Optional[datetime] = None
    last_opened_by: Optional[str] = None
    last_updated_at: Optional[datetime] = None
    last_updated_by: Optional[str] = None
    line_items: List[POLineItemOut] = []

    @computed_field
    @property
    def item(self) -> str:
        if self.line_items:
            return ", ".join(li.item for li in self.line_items)
        return ""

    @computed_field
    @property
    def uom(self) -> str:
        return self.line_items[0].uom if self.line_items else "Nos"

    @computed_field
    @property
    def total_quantity(self) -> float:
        if self.line_items:
            return sum(li.quantity for li in self.line_items)
        return 0

    @computed_field
    @property
    def delivered_quantity(self) -> float:
        if self.line_items:
            return sum(li.delivered_quantity for li in self.line_items)
        return 0

    @computed_field
    @property
    def pending_quantity(self) -> float:
        return max(0, self.total_quantity - self.delivered_quantity)

    @computed_field
    @property
    def gst_rate(self) -> float:
        if self.gst is None or self.gst.strip() in ("", "0"):
            return 0.0
        if self.gst.startswith("₹"):
            return 0.0
        cleaned = self.gst.replace("%", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    @computed_field
    @property
    def delivery_status(self) -> str:
        if self.delivered_quantity <= 0:
            return "Not Delivered"
        elif self.delivered_quantity >= self.total_quantity:
            return "Delivered"
        return "Partial"

    @computed_field
    @property
    def subtotal(self) -> float:
        if self.line_items:
            return sum(li.quantity * li.unit_price for li in self.line_items)
        return 0

    @computed_field
    @property
    def gst_amount(self) -> float:
        # 1. Global GST (if set and not "0")
        if self.gst and self.gst.strip() not in ("", "0"):
            if self.gst.startswith("₹"):
                cleaned = self.gst.replace("₹", "").replace(",", "").strip()
                try:
                    return float(cleaned)
                except ValueError:
                    return 0.0
            else:
                return self.subtotal * self.gst_rate / 100
        
        # 2. Sum line item GST
        if self.line_items:
            return sum(li.gst_amount for li in self.line_items)
            
        return 0.0

    @computed_field
    @property
    def grand_total(self) -> float:
        items_freight = sum(li.freight for li in self.line_items) if self.line_items else 0.0
        return self.subtotal + self.gst_amount + self.freight + items_freight

    all_dispatches_marked: bool = False

    model_config = {"from_attributes": True}

