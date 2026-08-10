from typing import Optional
from fastapi import HTTPException, status
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from app.config import settings
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
