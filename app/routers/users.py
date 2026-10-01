from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from jose import jwt
from datetime import datetime, timedelta
from typing import List, Optional
from app.database import get_db
from app.models.models import User, UserSession, ApplicationAccess, Application
from app.schemas.user import UserCreate, UserUpdate, UserOut, UserLogin, Token, UserSessionOut, PasswordResetRequest, PasswordResetConfirm, PasswordChange
from app.config import settings
from app.utils.helpers import log_activity
from app.utils.auth import get_user_id_from_token, require_admin, require_super_admin, get_current_user
from app.routers.application_access import serialize_user

import uuid
import asyncio
import secrets
import smtplib
from email.message import EmailMessage
from fastapi import WebSocket, WebSocketDisconnect
from app.services.session_manager import manager


router = APIRouter(prefix="/api/users", tags=["Users"])

# One-time codes stay only in server memory and expire quickly. They are not
# passwords and are removed immediately after successful use.
password_reset_codes: dict[str, dict] = {}

import bcrypt

def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": str(user_id), "exp": expire},
        settings.SECRET_KEY,
        algorithm="HS256",
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered.",
        )
    user = User(
        name=payload.name,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        is_active=False,  # Pending admin approval
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    log_activity(
        db, "User Registered", "User",
        f"New user {user.name} registered and is awaiting admin approval.",
        user.name, user.id,
        entity_name=user.name,
    )
    return user


@router.get("/pending", response_model=List[UserOut])
def list_pending_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_super_admin),
):
    """Super Admin only: users who registered but have not yet been approved."""
    return (
        db.query(User)
        .filter(User.is_active == False)
        .order_by(User.created_at.desc())
        .all()
    )


from pydantic import BaseModel as _BaseModel
class WorkspaceApproval(_BaseModel):
    workspace: str  # "marketing" | "store" | "both"

@router.post("/{user_id}/approve", response_model=UserOut)
def approve_user(
    user_id: int,
    payload: WorkspaceApproval,
    db: Session = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    """Super Admin only: approve a pending registration with workspace access."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    user.is_active = True

    # Grant access based on workspace selection
    workspace = (payload.workspace or "both").strip().lower()
    grant_marketing = workspace in ("marketing", "both")
    grant_store = workspace in ("store", "both")

    apps = {app.code: app for app in db.query(Application).all()}
    for code, should_grant in [("marketing", grant_marketing), ("store_purchase", grant_store)]:
        if code not in apps:
            continue
        row = db.query(ApplicationAccess).filter_by(user_id=user.id, application_id=apps[code].id).first()
        if should_grant:
            if not row:
                db.add(ApplicationAccess(user_id=user.id, application_id=apps[code].id, role="user"))
            elif row.role == "none":
                row.role = "user"
        else:
            # Set role to "none" rather than deleting — access_map returns "none"
            # which correctly restricts access in both frontend route guards and API.
            if row:
                row.role = "none"
            else:
                db.add(ApplicationAccess(user_id=user.id, application_id=apps[code].id, role="none"))

    db.commit()
    db.refresh(user)

    log_activity(
        db, "User Approved", "User",
        f"User {user.name} approved by {admin.name} with workspace: {payload.workspace}.",
        admin.name, user.id,
        entity_name=user.name,
    )
    return user


@router.put("/{user_id}/workspace", response_model=UserOut)
def update_user_workspace(
    user_id: int,
    payload: WorkspaceApproval,
    db: Session = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    """Super Admin only: change an approved user's workspace access."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="User has not been approved yet.")

    workspace = (payload.workspace or "both").strip().lower()
    grant_marketing = workspace in ("marketing", "both")
    grant_store = workspace in ("store", "both")

    apps = {app.code: app for app in db.query(Application).all()}
    for code, should_grant in [("marketing", grant_marketing), ("store_purchase", grant_store)]:
        if code not in apps:
            continue
        row = db.query(ApplicationAccess).filter_by(user_id=user.id, application_id=apps[code].id).first()
        if should_grant:
            if not row:
                db.add(ApplicationAccess(user_id=user.id, application_id=apps[code].id, role="user"))
            elif row.role == "none":
                row.role = "user"
        else:
            # Set role to "none" rather than deleting — access_map returns "none"
            # which correctly restricts access in both frontend route guards and API.
            if row:
                row.role = "none"
            else:
                db.add(ApplicationAccess(user_id=user.id, application_id=apps[code].id, role="none"))

    db.commit()
    db.refresh(user)

    log_activity(
        db, "Workspace Access Changed", "User",
        f"User {user.name} workspace changed to {payload.workspace} by {admin.name}.",
        admin.name, user.id,
        entity_name=user.name,
    )
    return user


@router.post("/{user_id}/reject")
def reject_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    """Super Admin only: reject a pending registration, removing it entirely."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.is_active:
        raise HTTPException(status_code=400, detail="User is already approved.")

    name = user.name
    db.delete(user)
    db.commit()

    log_activity(
        db, "User Rejected", "User",
        f"Registration request from {name} was rejected by {admin.name}.",
        admin.name, entity_name=name,
    )
    return {"message": "Registration request rejected."}


@router.post("/login", response_model=Token)
async def login(payload: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin has not approved your request yet.",
        )

    # Auto-ensure Super Admin status and application access for designated super admin emails
    from app.config import settings
    super_admins = [e.strip().lower() for e in settings.SUPER_ADMIN_EMAIL.split(",") if e.strip()]
    if user.email.lower() in super_admins or user.email.lower() == "deepikar412003@gmail.com":
        if not user.is_super_admin:
            user.is_super_admin = True
            db.commit()
        for code in ["marketing", "store_purchase"]:
            app = db.query(Application).filter_by(code=code).first()
            if app:
                acc = db.query(ApplicationAccess).filter_by(user_id=user.id, application_id=app.id).first()
                if not acc:
                    db.add(ApplicationAccess(user_id=user.id, application_id=app.id, role="admin"))
                elif acc.role != "admin":
                    acc.role = "admin"
        db.commit()
        
    # Check if user is active on another session
    if manager.is_user_active(user.id):
        request_id = str(uuid.uuid4())
        event = asyncio.Event()
        manager.pending_login_events[request_id] = event
        manager.pending_login_users[request_id] = user.id

        # Notify the active session
        await manager.notify_user(user.id, {
            "type": "LOGIN_ATTEMPT",
            "request_id": request_id,
            "message": "Someone is trying to log in to your account. Is this you?"
        })

        # Explicit confirmation is mandatory. A timeout or a rejected prompt
        # must never silently grant access to a second browser/device.
        try:
            await asyncio.wait_for(event.wait(), timeout=15.0)
            approved = manager.pending_login_results.get(request_id, False)
        except asyncio.TimeoutError:
            approved = False
        finally:
            manager.pending_login_events.pop(request_id, None)
            manager.pending_login_results.pop(request_id, None)
            manager.pending_login_users.pop(request_id, None)

        if not approved:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Login requires approval from the active session. Please click YES, IT'S ME there and try again.",
            )

    token = create_access_token(user.id)

    # Create session record
    session = UserSession(
        user_id=user.id,
        user_name=user.name,
        user_email=user.email,
        is_active=True,
    )
    db.add(session)
    db.commit()

    log_activity(
        db, "User Logged In", "User",
        f"User {user.name} logged in.",
        user.name, user.id,
        entity_name=user.name,
    )
    return Token(access_token=token, user=serialize_user(user))

from pydantic import BaseModel
class LoginApproval(BaseModel):
    request_id: str
    action: str # "approve" or "reject"

@router.post("/approve-login")
async def approve_login(payload: LoginApproval, authorization: str = Header(default=None)):
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token.")
    user_id = get_user_id_from_token(authorization)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")
    request_id = payload.request_id
    if request_id in manager.pending_login_events:
        if manager.pending_login_users.get(request_id) != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This login request belongs to another account.")
        manager.pending_login_results[request_id] = (payload.action == "approve")
        manager.pending_login_events[request_id].set()
        return {"message": "Action processed."}
    return {"message": "Invalid or expired request."}

@router.websocket("/ws/auth")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    # Get token from query param (WebSocket doesn't support auth headers easily from browser API)
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
        
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        user_id_str = payload.get("sub")
        if not user_id_str:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        user_id = int(user_id_str)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
        
    if user_id not in manager.active_connections:
        manager.active_connections[user_id] = []
    manager.active_connections[user_id].append(websocket)
    
    try:
        while True:
            # Keep connection alive, wait for client messages if any
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)
    except Exception as e:
        print("WebSocket Error:", e)
        manager.disconnect(websocket, user_id)



@router.post("/logout")
def logout(authorization: str = Header(default=None), db: Session = Depends(get_db)):
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token.")

    user_id = get_user_id_from_token(authorization)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Mark the most recent active session as logged out
    session = (
        db.query(UserSession)
        .filter(UserSession.user_id == user_id, UserSession.is_active == True)
        .order_by(UserSession.login_at.desc())
        .first()
    )
    if session:
        session.is_active = False
        session.logout_at = datetime.utcnow()
        db.commit()

    from app import notifications
    notifications.remove_user(user_id)

    log_activity(
        db, "User Logged Out", "User",
        f"User {user.name} logged out.",
        user.name, user.id,
        entity_name=user.name,
    )
    return {"message": "Logged out successfully."}


@router.post("/{user_id}/force-logout")
async def force_logout_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    """Super Admin only: forcefully log out any user session."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Mark all active sessions as logged out
    sessions = (
        db.query(UserSession)
        .filter(UserSession.user_id == user_id, UserSession.is_active == True)
        .all()
    )
    for session in sessions:
        session.is_active = False
        session.logout_at = datetime.utcnow()
    db.commit()

    from app import notifications
    notifications.remove_user(user_id)

    # Terminate active websocket connections for this user
    await manager.force_disconnect_user(
        user_id,
        {"type": "FORCE_LOGOUT", "message": "Your session has been terminated by the administrator."}
    )

    log_activity(
        db, "Force Logout", "User",
        f"User {user.name} was forcefully logged out by {admin.name}.",
        admin.name, user.id,
        entity_name=user.name,
    )
    return {"message": f"User {user.name} has been logged out."}


@router.post("/heartbeat", response_model=UserSessionOut)
def heartbeat(authorization: str = Header(default=None), db: Session = Depends(get_db)):
    """Called by the frontend on app load.

    Ensures that a user who has a valid JWT token (but no session record,
    e.g. they logged in before session tracking was added) is registered
    as online immediately — without needing to log out and back in.
    """
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token.")

    user_id = get_user_id_from_token(authorization)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.")

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Check if there is already an active session for this user
    existing = (
        db.query(UserSession)
        .filter(UserSession.user_id == user_id, UserSession.is_active == True)
        .order_by(UserSession.login_at.desc())
        .first()
    )
    if existing:
        return existing

    # No active session — create one now (covers users who logged in before
    # session tracking was deployed)
    session = UserSession(
        user_id=user.id,
        user_name=user.name,
        user_email=user.email,
        is_active=True,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.get("/active-sessions", response_model=List[UserSessionOut])
def active_sessions(db: Session = Depends(get_db), _: User = Depends(require_super_admin)):
    return (
        db.query(UserSession)
        .filter(UserSession.is_active == True)
        .order_by(UserSession.login_at.desc())
        .all()
    )


@router.get("/recent-logins", response_model=List[UserSessionOut])
def recent_logins(db: Session = Depends(get_db), _: User = Depends(require_super_admin)):
    """Returns the most recent login per user (up to 20 users), newest first.

    Online status is sourced from the live SSE connection list (notifications
    module) — not from the DB is_active flag — so a backend restart never
    causes an actively-connected user to appear as Offline in the History tab.
    """
    from app import notifications as notif

    # user_ids who currently have the app open (live SSE connection)
    live_user_ids = {u["user_id"] for u in notif.get_online_users()}

    all_sessions = (
        db.query(UserSession)
        .order_by(UserSession.login_at.desc())
        .limit(100)
        .all()
    )

    # Sync DB sessions with live status:
    # mark active if SSE is live, mark inactive if SSE is gone
    for s in all_sessions:
        if s.user_id in live_user_ids:
            s.is_active = True
        # Don't flip to False here — keep DB state for users not in SSE
        # (they may have just briefly disconnected)

    # Keep only the most recent session per user
    seen_users: set[int] = set()
    deduped = []
    for s in all_sessions:
        if s.user_id in seen_users:
            continue
        seen_users.add(s.user_id)
        # Use live SSE status as the definitive online indicator
        s.is_active = s.user_id in live_user_ids
        deduped.append(s)
        if len(deduped) >= 20:
            break

    return deduped


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_super_admin)):
    return db.query(User).filter(User.is_active == True).all()


@router.put("/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db), _: User = Depends(require_super_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if payload.name:
        user.name = payload.name
    if payload.email:
        user.email = payload.email
    if payload.password:
        user.hashed_password = hash_password(payload.password)
    db.commit()
    db.refresh(user)
    log_activity(
        db, "User Updated", "User",
        f"User {user.name} was updated.",
        "System/Admin", user.id,
        entity_name=user.name,
    )
    return user


def send_password_reset_otp(recipient: str, otp: str) -> None:
    if not all([settings.SMTP_HOST, settings.SMTP_USERNAME, settings.SMTP_PASSWORD, settings.SMTP_FROM_EMAIL]):
        raise HTTPException(status_code=503, detail="Password-reset email is not configured. Ask the system administrator to configure SMTP.")
    message = EmailMessage()
    message["Subject"] = "JB Engineering password reset code"
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = recipient
    message.set_content(f"Your JB Engineering password reset code is: {otp}\n\nThis code expires in {settings.PASSWORD_RESET_OTP_EXPIRE_MINUTES} minutes. Do not share it with anyone.")
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
            if settings.SMTP_USE_TLS:
                smtp.starttls()
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(message)
    except Exception:
        raise HTTPException(status_code=503, detail="Could not send reset email. Please try again later.")


@router.post("/password-reset/request")
def request_password_reset(payload: PasswordResetRequest, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    user = db.query(User).filter(User.email == email, User.is_active == True).first()
    # Same response prevents an attacker from discovering which emails exist.
    if not user:
        return {"message": "If this email is registered, an OTP has been sent."}
    otp = f"{secrets.randbelow(1_000_000):06d}"
    send_password_reset_otp(user.email, otp)
    password_reset_codes[email] = {"otp": otp, "expires_at": datetime.utcnow() + timedelta(minutes=settings.PASSWORD_RESET_OTP_EXPIRE_MINUTES), "attempts": 0}
    return {"message": "If this email is registered, an OTP has been sent."}


@router.post("/password-reset/confirm")
def confirm_password_reset(payload: PasswordResetConfirm, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    stored = password_reset_codes.get(email)
    if not stored or datetime.utcnow() > stored["expires_at"]:
        password_reset_codes.pop(email, None)
        raise HTTPException(status_code=400, detail="OTP expired or invalid. Request a new code.")
    if stored["attempts"] >= 5:
        password_reset_codes.pop(email, None)
        raise HTTPException(status_code=429, detail="Too many incorrect OTP attempts. Request a new code.")
    if not secrets.compare_digest(stored["otp"], payload.otp.strip()):
        stored["attempts"] += 1
        raise HTTPException(status_code=400, detail="Incorrect OTP.")
    if len(payload.password) < 8:
        raise HTTPException(status_code=422, detail="Password must contain at least 8 characters.")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=400, detail="OTP expired or invalid. Request a new code.")
    user.hashed_password = hash_password(payload.password)
    password_reset_codes.pop(email, None)
    db.commit()
    log_activity(
        db, "Password Reset", "User",
        f"User {user.name} reset their password.",
        user.name, user.id,
        entity_name=user.name,
    )
    return {"message": "Password updated successfully."}


@router.post("/password/change")
def change_password(payload: PasswordChange, db: Session = Depends(get_db)):
    """Simple no-email password change: knowledge of the current password is
    required, so an email address alone cannot be used to take over an account."""
    user = db.query(User).filter(User.email == payload.email.strip().lower(), User.is_active == True).first()
    if not user or not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email or current password is incorrect.")
    user.hashed_password = hash_password(payload.password)
    db.commit()
    log_activity(db, "Password Changed", "User", f"User {user.name} changed their password.", user.name, user.id, entity_name=user.name)
    return {"message": "Password updated successfully."}
