from pydantic import BaseModel
from datetime import datetime
from typing import Optional
from app.models.models import PaymentStatus, DeliveryStatus


class RecordCreate(BaseModel):
    client_name: str
    product: str
    price: float
    location: Optional[str] = None
    payment_status: PaymentStatus = PaymentStatus.PENDING
    delivery_status: DeliveryStatus = DeliveryStatus.NOT_DELIVERED
    po_number: Optional[str] = None
    invoice_number: Optional[str] = None
    date: Optional[datetime] = None


class RecordUpdate(BaseModel):
    payment_status: Optional[PaymentStatus] = None
    delivery_status: Optional[DeliveryStatus] = None
    invoice_number: Optional[str] = None


class RecordOut(BaseModel):
    id: int
    client_name: str
    product: str
    price: float
    location: Optional[str]
    payment_status: PaymentStatus
    delivery_status: DeliveryStatus
    po_number: Optional[str]
    invoice_number: Optional[str]
    date: datetime
    created_at: datetime

    model_config = {"from_attributes": True}
