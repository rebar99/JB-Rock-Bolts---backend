from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class ProjectCreate(BaseModel):
    name: str
    client_name: str
    created_by: Optional[str] = None

class ProjectOut(BaseModel):
    id: int
    name: str
    client_id: int
    created_at: datetime

    model_config = {"from_attributes": True}
