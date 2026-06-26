from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional
from app.schemas.base import UTCDatetime, OptUTCDatetime


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str


class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = None


class UserOut(BaseModel):
    id: int
    name: str
    email: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserLogin(BaseModel):
    email: EmailStr
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
