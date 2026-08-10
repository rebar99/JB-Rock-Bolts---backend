from pydantic import BaseModel, computed_field
from datetime import datetime
from typing import Optional, List
from app.schemas.base import UTCDatetime, OptUTCDatetime

class WOLineItemBase(BaseModel):
    item: str
    quantity: float = 0
    completed_quantity: float = 0
    uom: str = "Nos"
    unit_price: float = 0
    gst: Optional[str] = "0"
    freight: float = 0

class WOLineItemCreate(WOLineItemBase):
    id: Optional[int] = None

class WOLineItemOut(WOLineItemBase):
    id: int

    @computed_field
    @property
    def pending_quantity(self) -> float:
        return round(max(0, self.quantity - self.completed_quantity), 10)

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

class WorkOrderBase(BaseModel):
    client_name: str
    wo_number: str
    project: Optional[str] = None
    project_id: Optional[int] = None
    wo_date: Optional[datetime] = None
    gst: Optional[str] = "0"
    freight: float = 0
    work_description: Optional[str] = None
    site_location: Optional[str] = None
    engineer_name: Optional[str] = None
    priority: Optional[str] = "Medium"
    start_date: Optional[datetime] = None
    target_completion_date: Optional[datetime] = None
    remarks: Optional[str] = None
    status: Optional[str] = "Pending"
    file_url: Optional[str] = None

class WorkOrderCreate(WorkOrderBase):
    created_by: Optional[str] = None
    line_items: List[WOLineItemCreate] = []

class WorkOrderUpdate(BaseModel):
    client_name: Optional[str] = None
    wo_number: Optional[str] = None
    project: Optional[str] = None
    project_id: Optional[int] = None
    wo_date: Optional[datetime] = None
    gst: Optional[str] = None
    freight: Optional[float] = None
    work_description: Optional[str] = None
    site_location: Optional[str] = None
    engineer_name: Optional[str] = None
    priority: Optional[str] = None
    start_date: Optional[datetime] = None
    target_completion_date: Optional[datetime] = None
    remarks: Optional[str] = None
    status: Optional[str] = None
    file_url: Optional[str] = None
    last_updated_by: Optional[str] = None
    line_items: Optional[List[WOLineItemCreate]] = None


class WorkOrderClose(BaseModel):
    remark: Optional[str] = None
    user: Optional[str] = None

class WorkOrderOut(WorkOrderBase):
    id: int
    created_at: UTCDatetime
    created_by: Optional[str] = None
    last_opened_at: Optional[UTCDatetime] = None
    last_opened_by: Optional[str] = None
    last_updated_at: Optional[UTCDatetime] = None
    last_updated_by: Optional[str] = None
    line_items: List[WOLineItemOut] = []
    closed_at: Optional[datetime] = None
    closed_by: Optional[str] = None
    closed_remark: Optional[str] = None
    # True only on the response to PUT /{wo_id} when nothing actually
    # differed from what was already stored — lets the frontend show "No
    # changes to save" instead of a misleading "Updated" toast. Always
    # False on every other response (GET, POST, etc).
    no_changes: bool = False

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
    def completed_quantity(self) -> float:
        if self.line_items:
            return sum(li.completed_quantity for li in self.line_items)
        return 0

    @computed_field
    @property
    def pending_quantity(self) -> float:
        return round(max(0, self.total_quantity - self.completed_quantity), 10)

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
    def subtotal(self) -> float:
        if self.line_items:
            return sum(li.quantity * li.unit_price for li in self.line_items)
        return 0

    @computed_field
    @property
    def gst_amount(self) -> float:
        if self.gst and self.gst.strip() not in ("", "0"):
            if self.gst.startswith("₹"):
                cleaned = self.gst.replace("₹", "").replace(",", "").strip()
                try:
                    return float(cleaned)
                except ValueError:
                    return 0.0
            else:
                return self.subtotal * self.gst_rate / 100

        if self.line_items:
            return sum(li.gst_amount for li in self.line_items)

        return 0.0

    @computed_field
    @property
    def grand_total(self) -> float:
        items_freight = sum(li.freight for li in self.line_items) if self.line_items else 0.0
        return self.subtotal + self.gst_amount + self.freight + items_freight

    model_config = {"from_attributes": True}
