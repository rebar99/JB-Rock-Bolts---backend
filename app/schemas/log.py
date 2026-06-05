from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class SystemLogOut(BaseModel):
    id: int
    action: str
    entity_type: str
    entity_id: Optional[int]
    details: Optional[str]
    user: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}
