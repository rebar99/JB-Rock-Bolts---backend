from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class UOMOptionBase(BaseModel):
    name: str


class UOMOptionCreate(UOMOptionBase):
    pass


class UOMOptionUpdate(UOMOptionBase):
    pass


class UOMOptionOut(UOMOptionBase):
    id: int
    created_at: datetime
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = {"from_attributes": True}
