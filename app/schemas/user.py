from pydantic import BaseModel
from datetime import datetime
from typing import Optional
from app.schemas.base import UTCDatetime, OptUTCDatetime


class UserCreate(BaseModel):
    name: str
    email: str  # Change EmailStr to str
    password: str


class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None  # Change Optional[EmailStr] to Optional[str]
    password: Optional[str] = None


class UserOut(BaseModel):
    id: int
    name: str
    email: str
    is_active: bool
    is_admin: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class UserLogin(BaseModel):
    email: str  # Change EmailStr to str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserSessionOut(BaseModel):
    id: int
    user_id: int
    user_name: str
    user_email: str
    login_at: UTCDatetime
    logout_at: Optional[OptUTCDatetime] = None
    is_active: bool

    model_config = {"from_attributes": True}
