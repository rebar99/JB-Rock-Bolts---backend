from pydantic import BaseModel
from typing import Optional
from app.schemas.base import UTCDatetime


class SystemLogOut(BaseModel):
    id: int
    action: str
    entity_type: str
    entity_id: Optional[int]
    details: Optional[str]
    user: Optional[str]
    created_at: UTCDatetime

    model_config = {"from_attributes": True}
