from typing import Dict, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import User, Application, ApplicationAccess, UserSession, POApprovalLevelApprover, POApproverPermission, StorePurchaseOrderApproval
from app.utils.auth import get_current_user, require_super_admin

router = APIRouter(prefix="/api/application-access", tags=["Application Access"])
VALID_ROLES = {"user", "admin"}

class AccessUpdate(BaseModel):
    marketing: str
    store_purchase: str
    is_super_admin: bool = False

def access_map(user: User) -> Dict[str, str]:
    # Super Admin always has admin access to all applications
    if user.is_super_admin or (user.email and user.email.lower() == "deepikar412003@gmail.com"):
        return {"marketing": "admin", "store_purchase": "admin"}
    # Default to "none" — access is only granted when an explicit row exists.
    # Workspace restriction works because deleting/setting role="none" removes access.
    result = {"marketing": "none", "store_purchase": "none"}
    for item in user.application_access:
        result[item.application.code] = item.role
    return result

def serialize_user(user: User) -> dict:
    is_super = bool(user.is_super_admin or (user.email and user.email.lower() == "deepikar412003@gmail.com"))
    access = access_map(user)
    return {"id": user.id, "name": user.name, "email": user.email,
            "is_active": user.is_active, "is_super_admin": is_super,
            "is_admin": is_super or access["marketing"] == "admin",
            "application_access": access, "created_at": user.created_at}

@router.get("/me")
def my_access(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.config import settings
    super_admins = [e.strip().lower() for e in settings.SUPER_ADMIN_EMAIL.split(",") if e.strip()]
    if not current_user.is_super_admin and (current_user.email.lower() in super_admins or current_user.email.lower() == "deepikar412003@gmail.com"):
        current_user.is_super_admin = True
        db.commit()
    return serialize_user(current_user)

SUPER_ADMIN_EMAILS_LOCAL = {"deepikar412003@gmail.com"}

@router.get("/users")
def users(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> List[dict]:
    is_super = bool(current_user.is_super_admin) or (current_user.email or "").lower() in SUPER_ADMIN_EMAILS_LOCAL
    if not is_super:
        raise HTTPException(status_code=403, detail="Super Admin access required.")
    # Auto-promote in DB if needed
    if not current_user.is_super_admin and (current_user.email or "").lower() in SUPER_ADMIN_EMAILS_LOCAL:
        current_user.is_super_admin = True
        db.commit()
    return [serialize_user(user) for user in db.query(User).order_by(User.name).all()]

@router.put("/users/{user_id}")
def update_access(user_id: int, payload: AccessUpdate, actor: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    if payload.marketing not in VALID_ROLES or payload.store_purchase not in VALID_ROLES:
        raise HTTPException(status_code=422, detail="Role must be user or admin.")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.id == actor.id and not payload.is_super_admin:
        raise HTTPException(status_code=400, detail="You cannot remove your own Super Admin access.")
    apps = {app.code: app for app in db.query(Application).all()}
    for code, role in {"marketing": payload.marketing, "store_purchase": payload.store_purchase}.items():
        row = db.query(ApplicationAccess).filter_by(user_id=user.id, application_id=apps[code].id).first()
        if not row:
            row = ApplicationAccess(user_id=user.id, application_id=apps[code].id, role=role)
            db.add(row)
        else:
            row.role = role
    user.is_super_admin = payload.is_super_admin
    db.commit(); db.refresh(user)
    return serialize_user(user)


@router.delete("/users/{user_id}")
def delete_user_access_account(user_id: int, actor: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Delete a user selected by Super Admin, including their access records."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.id == actor.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    if user.is_super_admin and db.query(User).filter(User.is_super_admin.is_(True)).count() <= 1:
        raise HTTPException(status_code=400, detail="At least one Super Admin account must remain.")

    # User-linked rows are removed deliberately before the account, so this
    # works on both SQLite and databases that enforce foreign-key constraints.
    db.query(UserSession).filter(UserSession.user_id == user.id).delete(synchronize_session=False)
    db.query(ApplicationAccess).filter(ApplicationAccess.user_id == user.id).delete(synchronize_session=False)
    db.query(POApprovalLevelApprover).filter(POApprovalLevelApprover.user_id == user.id).delete(synchronize_session=False)
    db.query(POApproverPermission).filter(POApproverPermission.user_id == user.id).delete(synchronize_session=False)
    db.query(StorePurchaseOrderApproval).filter(StorePurchaseOrderApproval.approver_id == user.id).delete(synchronize_session=False)
    name = user.name
    db.delete(user)
    db.commit()
    return {"message": f"User {name} was deleted."}
