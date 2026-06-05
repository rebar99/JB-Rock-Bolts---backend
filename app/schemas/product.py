from pydantic import BaseModel
from datetime import datetime
from typing import Optional
from app.models.models import InventoryStatus


class ProductCreate(BaseModel):
    name: str
    quantity: int = 0
    status: Optional[InventoryStatus] = None


class ProductUpdate(BaseModel):
    quantity: Optional[int] = None
    status: Optional[InventoryStatus] = None


class ProductOut(BaseModel):
    id: int
    name: str
    quantity: int
    status: InventoryStatus
    sales_count: Optional[int] = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
