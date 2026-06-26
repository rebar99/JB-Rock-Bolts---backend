from pydantic import BaseModel
from typing import Optional
from app.schemas.base import UTCDatetime


class SystemLogOut(BaseModel):
    id: int
    action: str
    entity_type: str
    entity_id: Optional[int] = None
    entity_name: Optional[str] = None
    details: Optional[str] = None
    changed_fields: Optional[str] = None
    status: Optional[str] = "Success"
    user: Optional[str] = None
    created_at: UTCDatetime

    model_config = {"from_attributes": True}
