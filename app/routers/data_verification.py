"""
Data Verification System – Marketing Reports
==============================================
Endpoints:
  GET  /api/data-verification/status          → current verification status (all users)
  POST /api/data-verification/verify          → authorized user verifies data
  GET  /api/data-verification/history         → full audit log (super admin or verifier)
  GET  /api/data-verification/permissions     → list users who can verify (super admin)
  POST /api/data-verification/permissions     → grant permission (super admin)
  DELETE /api/data-verification/permissions/{user_id} → revoke (super admin)
  GET  /api/data-verification/eligible-users  → all active users list (super admin, for picker)
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import User, DataVerificationPermission, DataVerificationRecord
from app.utils.auth import get_current_user, require_super_admin, get_user_id_from_token
from app.utils.helpers import log_activity

router = APIRouter(prefix="/api/data-verification", tags=["Data Verification"])

VALIDITY_DAYS = 7
SCOPE = "marketing_report"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # store as naive UTC


def _can_verify(user_id: int, db: Session) -> bool:
    """Returns True if the user has been granted verification permission."""
    return db.query(DataVerificationPermission).filter_by(user_id=user_id).first() is not None


from sqlalchemy import event, text

def _latest_record(db: Session, scope: str = SCOPE) -> Optional[DataVerificationRecord]:
    return (
        db.query(DataVerificationRecord)
        .filter_by(scope=scope)
        .order_by(DataVerificationRecord.verified_at.desc())
        .first()
    )


def _active_record(db: Session, scope: str = SCOPE) -> Optional[DataVerificationRecord]:
    return (
        db.query(DataVerificationRecord)
        .filter_by(scope=scope, is_active=True)
        .order_by(DataVerificationRecord.verified_at.desc())
        .first()
    )


def _compute_status(db: Session) -> dict:
    """Compute the verification status dict from the active and latest records."""
    now = _now_utc()
    active = _active_record(db)
    latest = _latest_record(db)

    if not latest:
        return {
            "status": "Pending Verification",
            "verified_by": None,
            "verified_by_id": None,
            "verified_date": None,
            "next_verification_date": None,
            "last_verification_date": None,
            "expires_at": None,
        }

    # If there is an active record
    if active:
        is_expired = now > active.expires_at
        status_val = "Expired" if is_expired else "Verified"
        return {
            "status": status_val,
            "verified_by": active.verified_by.name if active.verified_by else None,
            "verified_by_id": active.verified_by_user_id,
            "verified_date": active.verified_at.isoformat() + "Z",
            "next_verification_date": active.expires_at.isoformat() + "Z",
            "last_verification_date": active.verified_at.isoformat() + "Z",
            "expires_at": active.expires_at.isoformat() + "Z",
        }

    # No active record (e.g. data edited or expired)
    return {
        "status": "Pending Verification",
        "verified_by": latest.verified_by.name if latest.verified_by else None,
        "verified_by_id": latest.verified_by_user_id,
        "verified_date": latest.verified_at.isoformat() + "Z",
        "next_verification_date": latest.expires_at.isoformat() + "Z",
        "last_verification_date": latest.verified_at.isoformat() + "Z",
        "expires_at": latest.expires_at.isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# Invalidation Hooks: automatically mark active verification as pending
# whenever Marketing PO, Sale, LineItem, WorkOrder, or CreditNote is changed.
# ---------------------------------------------------------------------------

def _setup_listeners():
    from app.models.models import PurchaseOrder, POLineItem, Sale, WorkOrder, CreditNote

    def _invalidate_hook(mapper, connection, target):
        try:
            connection.execute(
                text("UPDATE data_verification_records SET is_active = 0 WHERE scope = 'marketing_report' AND is_active = 1")
            )
        except Exception:
            pass

    for model in [PurchaseOrder, POLineItem, Sale, WorkOrder, CreditNote]:
        event.listen(model, "after_insert", _invalidate_hook)
        event.listen(model, "after_update", _invalidate_hook)
        event.listen(model, "after_delete", _invalidate_hook)

try:
    _setup_listeners()
except Exception:
    pass


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class VerifyRequest(BaseModel):
    notes: Optional[str] = None


class GrantPermissionRequest(BaseModel):
    user_id: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/status")
def get_verification_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Public (any authenticated user): returns current verification status."""
    result = _compute_status(db)
    # Tell the frontend whether the current user can verify
    result["can_verify"] = _can_verify(current_user.id, db) or current_user.is_super_admin
    result["is_super_admin"] = current_user.is_super_admin
    return result


@router.post("/verify")
def verify_data(
    payload: VerifyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Authorized verifier: creates a new verification record (7-day validity)."""
    if not (_can_verify(current_user.id, db) or current_user.is_super_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to verify data.",
        )

    now = _now_utc()

    # If already verified and active (within 7 days), do not allow repeated verification
    active = _active_record(db)
    if active and now <= active.expires_at:
        next_date_str = active.expires_at.strftime("%d %b %Y")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Data is already verified. Re-verification can only be done after 7 days (Next verification: {next_date_str}).",
        )

    expires = now + timedelta(days=VALIDITY_DAYS)

    # Deactivate any previous active record
    old = active
    if old:
        old.is_active = False

    new_record = DataVerificationRecord(
        scope=SCOPE,
        verified_by_user_id=current_user.id,
        verified_at=now,
        expires_at=expires,
        is_active=True,
        notes=payload.notes,
    )
    db.add(new_record)
    db.commit()
    db.refresh(new_record)

    log_activity(
        db, "Data Verified", "DataVerification",
        f"Marketing Report data verified by {current_user.name}. Valid until {expires.date()}.",
        current_user.name, current_user.id,
        entity_name="Marketing Report",
    )

    return {
        "message": "Data verified successfully.",
        **_compute_status(db),
        "can_verify": True,
        "is_super_admin": current_user.is_super_admin,
    }


@router.get("/history")
def get_verification_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns full audit history. Accessible to super admin and authorized verifiers."""
    if not (_can_verify(current_user.id, db) or current_user.is_super_admin):
        raise HTTPException(status_code=403, detail="Access denied.")

    records = (
        db.query(DataVerificationRecord)
        .filter_by(scope=SCOPE)
        .order_by(DataVerificationRecord.verified_at.desc())
        .all()
    )

    now = _now_utc()
    return [
        {
            "id": r.id,
            "verified_by": r.verified_by.name if r.verified_by else "Unknown",
            "verified_by_id": r.verified_by_user_id,
            "verified_at": r.verified_at.isoformat() + "Z",
            "expires_at": r.expires_at.isoformat() + "Z",
            "status": "Active" if r.is_active and now <= r.expires_at
                      else ("Expired" if r.is_active else "Superseded"),
            "notes": r.notes,
        }
        for r in records
    ]


@router.get("/permissions")
def list_permissions(
    db: Session = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    """Super Admin only: list all users who have been granted verification access."""
    perms = db.query(DataVerificationPermission).all()
    return [
        {
            "id": p.id,
            "user_id": p.user_id,
            "user_name": p.user.name if p.user else None,
            "user_email": p.user.email if p.user else None,
            "granted_by": p.granted_by_user.name if p.granted_by_user else None,
            "granted_at": p.granted_at.isoformat() + "Z" if p.granted_at else None,
        }
        for p in perms
    ]


@router.post("/permissions")
def grant_permission(
    payload: GrantPermissionRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    """Super Admin only: grant a user the ability to verify data."""
    target_user = db.get(User, payload.user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found.")

    existing = db.query(DataVerificationPermission).filter_by(user_id=payload.user_id).first()
    if existing:
        return {"message": "User already has verification permission."}

    perm = DataVerificationPermission(
        user_id=payload.user_id,
        granted_by=admin.id,
    )
    db.add(perm)
    db.commit()

    log_activity(
        db, "Verification Permission Granted", "DataVerification",
        f"{admin.name} granted data verification permission to {target_user.name}.",
        admin.name, admin.id,
        entity_name=target_user.name,
    )

    return {"message": f"Permission granted to {target_user.name}."}


@router.delete("/permissions/{user_id}")
def revoke_permission(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    """Super Admin only: revoke a user's verification permission."""
    perm = db.query(DataVerificationPermission).filter_by(user_id=user_id).first()
    if not perm:
        raise HTTPException(status_code=404, detail="Permission not found.")

    target_name = perm.user.name if perm.user else str(user_id)
    db.delete(perm)
    db.commit()

    log_activity(
        db, "Verification Permission Revoked", "DataVerification",
        f"{admin.name} revoked data verification permission from {target_name}.",
        admin.name, admin.id,
        entity_name=target_name,
    )

    return {"message": f"Permission revoked from {target_name}."}


@router.get("/eligible-users")
def list_eligible_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    """Super Admin only: all active users (to populate permission picker)."""
    users = db.query(User).filter(User.is_active == True).order_by(User.name).all()
    # Include which ones already have permission
    perm_ids = {p.user_id for p in db.query(DataVerificationPermission).all()}
    return [
        {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "is_super_admin": u.is_super_admin,
            "has_permission": u.id in perm_ids,
        }
        for u in users
    ]
