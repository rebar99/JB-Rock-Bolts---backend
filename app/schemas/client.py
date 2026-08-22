from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List

from .project import ProjectOut


class ClientCreate(BaseModel):
    name: str
    location: str
    created_by: Optional[str] = None


class MergeClientsRequest(BaseModel):
    master_id: int
    duplicate_ids: List[int]
    merged_by: Optional[str] = None


class ClientOut(BaseModel):
    id: int
    name: str
    location: str
    created_at: datetime
    order_count: Optional[int] = 0
    total_purchases: Optional[float] = 0.0
    projects: Optional[List[ProjectOut]] = []

    model_config = {"from_attributes": True}


class ClientStats(BaseModel):
    total_clients: int
