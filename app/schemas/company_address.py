from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class CompanyAddressBase(BaseModel):
    title: str
    address_text: str
    is_default: bool = False


class CompanyAddressCreate(CompanyAddressBase):
    pass


class CompanyAddressUpdate(BaseModel):
    title: Optional[str] = None
    address_text: Optional[str] = None
    is_default: Optional[bool] = None


class CompanyAddressOut(CompanyAddressBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
