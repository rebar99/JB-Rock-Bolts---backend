from pydantic import BaseModel
from datetime import date
from typing import Optional, List
from app.schemas.base import UTCDatetime

CREDIT_NOTE_REASONS = [
    "Quantity Less",
    "Quantity Excess",
    "Wrong Quantity",
    "Wrong Amount",
    "Wrong Product",
    "Wrong Address",
    "GST Correction",
    "Other",
]


class CreditNoteItemCreate(BaseModel):
    item: str
    uom: str = "Nos"
    original_qty: float = 0
    credit_qty: float
    unit_price: float
    gst_rate: float = 0
    subtotal: float = 0
    gst_amount: float = 0
    total_amount: float = 0


class CreditNoteItemOut(BaseModel):
    id: int
    credit_note_id: int
    item: str
    uom: str
    original_qty: float
    credit_qty: float
    unit_price: float
    gst_rate: float
    subtotal: float
    gst_amount: float
    total_amount: float
    model_config = {"from_attributes": True}


class CreditNoteCreate(BaseModel):
    cn_date: date
    sale_type: str
    sale_id: Optional[int] = None
    wo_sale_id: Optional[int] = None
    invoice_number: Optional[str] = None
    po_number: Optional[str] = None
    client_name: str
    project: Optional[str] = None
    reason: str
    taxable_amount: float = 0
    gst_amount: float = 0
    total_amount: float = 0
    # A credit note may refer to an invoice that predates this application.
    # In that case neither sale id is present and the invoice details below
    # are the user-entered source of truth.
    items: List[CreditNoteItemCreate] = []
    created_by: Optional[str] = None


class CreditNoteUpdate(BaseModel):
    sale_id: Optional[int] = None
    wo_sale_id: Optional[int] = None
    invoice_number: Optional[str] = None
    po_number: Optional[str] = None
    client_name: Optional[str] = None
    project: Optional[str] = None
    cn_date: Optional[date] = None
    reason: Optional[str] = None
    taxable_amount: Optional[float] = None
    gst_amount: Optional[float] = None
    total_amount: Optional[float] = None
    items: Optional[List[CreditNoteItemCreate]] = None
    status: Optional[str] = None
    updated_by: Optional[str] = None


class CreditNoteOut(BaseModel):
    id: int
    cn_number: str
    cn_date: date
    sale_type: str
    sale_id: Optional[int] = None
    wo_sale_id: Optional[int] = None
    invoice_number: Optional[str] = None
    po_number: Optional[str] = None
    client_name: str
    project: Optional[str] = None
    reason: str
    taxable_amount: float
    gst_amount: float
    total_amount: float
    status: str
    created_by: Optional[str] = None
    created_at: UTCDatetime
    updated_by: Optional[str] = None
    updated_at: Optional[UTCDatetime] = None
    is_deleted: bool = False
    items: List[CreditNoteItemOut] = []
    model_config = {"from_attributes": True}


class AlreadyCreditedItem(BaseModel):
    item: str
    already_credited_qty: float
