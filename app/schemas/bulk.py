from pydantic import BaseModel
from typing import List, Optional


class BulkDeleteRequest(BaseModel):
    ids: List[int]
    deleted_by: Optional[str] = None


class BulkDeleteResult(BaseModel):
    deleted: List[int]
    errors: List[str]
