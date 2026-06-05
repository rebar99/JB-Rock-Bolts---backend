from pydantic import BaseModel
from datetime import datetime

class ProjectCreate(BaseModel):
    name: str
    client_name: str

class ProjectOut(BaseModel):
    id: int
    name: str
    client_id: int
    created_at: datetime

    model_config = {"from_attributes": True}
