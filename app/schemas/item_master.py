from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional


class ItemMasterCreate(BaseModel):
    name: str
    created_by: Optional[str] = None


class ItemMasterUpdate(BaseModel):
    name: str
    updated_by: Optional[str] = None


class ItemMasterSizeCreate(BaseModel):
    size: str
    created_by: Optional[str] = None


class ItemMasterSizeOut(BaseModel):
    id: int
    size: str

    model_config = {"from_attributes": True}


class ItemMasterOut(BaseModel):
    id: int
    name: str
    sizes: List[ItemMasterSizeOut] = []
    created_at: datetime
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = {"from_attributes": True}
