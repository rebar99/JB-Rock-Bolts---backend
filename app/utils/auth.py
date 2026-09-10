from typing import Optional
from fastapi import Depends, Header, HTTPException, status
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models.models import User


def get_user_id_from_token(authorization: str) -> Optional[int]:
    try:
        scheme, token = authorization.split(" ", 1)
        if scheme.lower() != "bearer":
            return None
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        user_id = int(payload.get("sub"))
        return user_id
    except (JWTError, ValueError, AttributeError):
        return None


def _decode_raw_token(token: str) -> Optional[int]:
    """Decode a raw JWT string (without 'Bearer ' prefix) and return user_id."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        return int(payload.get("sub"))
    except (JWTError, ValueError, AttributeError):
        return None


def get_current_user(
    authorization: str = Header(..., alias="Authorization"),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: extracts Bearer token, validates JWT, returns User.

    Usage:  current_user: User = Depends(get_current_user)
    """
    user_id = get_user_id_from_token(authorization)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        )
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive.",
        )
    return user


def get_current_user_from_token(
    token: Optional[str] = None,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> User:
    """Like get_current_user but also accepts a ?token= query param.

    Useful for document routes opened via window.open() in a new tab.
    Priority: Authorization header > query param.
    """
    user_id = None
    if authorization:
        user_id = get_user_id_from_token(authorization)
    if not user_id and token:
        user_id = _decode_raw_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        )
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive.",
        )
    return user


def require_admin(authorization: str, db: Session, detail: str = "Admin access required.") -> User:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token.")
    user_id = get_user_id_from_token(authorization)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.")
    user = db.get(User, user_id)
    if not user or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return user
