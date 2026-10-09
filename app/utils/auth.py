from typing import Optional
from fastapi import Depends, Header, HTTPException, status
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models.models import User, Application, ApplicationAccess


def application_role(user: User, db: Session, application_code: str) -> str:
    if user.is_super_admin:
        return "admin"
    row = (db.query(ApplicationAccess.role)
        .join(Application)
        .filter(ApplicationAccess.user_id == user.id, Application.code == application_code)
        .first())
    # Both applications are available to every approved user.  A missing row
    # is deliberately treated as read-only user access, never as a denial.
    return row[0] if row and row[0] == "admin" else "user"


def require_application_access(user: User, db: Session, application_code: str, admin: bool = False) -> User:
    role = application_role(user, db, application_code)
    if role == "none":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"No access to {application_code}.")
    if admin and role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"{application_code} admin access required.")
    return user


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


SUPER_ADMIN_EMAILS = {"deepikar412003@gmail.com"}

def require_super_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    is_super = bool(current_user.is_super_admin) or (current_user.email or "").lower() in SUPER_ADMIN_EMAILS
    if not is_super:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super Admin access required.")
    return current_user


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


def require_admin(authorization: str, db: Session, detail: str = "Admin access required.", app_code: str = "marketing") -> User:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token.")
    user_id = get_user_id_from_token(authorization)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.")
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    if application_role(user, db, app_code) != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return user


def require_admin_for_write(
    authorization: str = Header(..., alias="Authorization"),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency for all mutating (POST/PUT/DELETE) endpoints.

    Allows admin users to proceed; raises HTTP 403 for regular (non-admin) users.
    Regular users have read-only access — they can call GET endpoints freely but
    cannot create, update, delete, or upload any data.

    Usage:  current_user: User = Depends(require_admin_for_write)
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
    if application_role(user, db, "marketing") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to perform this action. Read-only access only.",
        )
    return user

