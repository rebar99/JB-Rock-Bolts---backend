from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from typing import Optional, Dict, Any
from app.schemas.base import UTCDatetime, OptUTCDatetime


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str = Field(min_length=8)


class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(default=None, min_length=8)


class UserOut(BaseModel):
    id: int
    name: str
    email: str
    is_active: bool
    is_admin: bool = False  # Marketing compatibility field; derived from application access
    is_super_admin: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    email: EmailStr
    otp: str = Field(pattern=r"^\d{6}$")
    password: str = Field(min_length=8)


class PasswordChange(BaseModel):
    email: EmailStr
    current_password: str
    password: str = Field(min_length=8)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # Login includes a flattened access map; ordinary UserOut responses remain
    # ORM-compatible for pending-user and approval endpoints.
    user: Dict[str, Any]


class UserSessionOut(BaseModel):
    id: int
    user_id: int
    user_name: str
    user_email: str
    login_at: UTCDatetime
    logout_at: Optional[OptUTCDatetime] = None
    is_active: bool

    model_config = {"from_attributes": True}
