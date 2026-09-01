from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from jose import jwt
from datetime import datetime, timedelta
from typing import List
from app.database import get_db
from app.models.models import User, UserSession
from app.schemas.user import UserCreate, UserUpdate, UserOut, UserLogin, Token, UserSessionOut
from app.config import settings
from app.utils.helpers import log_activity
from app.utils.auth import get_user_id_from_token, require_admin

import uuid
import asyncio
from fastapi import WebSocket, WebSocketDisconnect
from app.services.session_manager import manager


router = APIRouter(prefix="/api/users", tags=["Users"])

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
        is_active=False,
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
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    """Admin-only: users who registered but have not yet been approved."""
    require_admin(authorization, db)
    return (
        db.query(User)
        .filter(User.is_active == False)
        .order_by(User.created_at.desc())
        .all()
    )


@router.post("/{user_id}/approve", response_model=UserOut)
def approve_user(
    user_id: int,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    """Admin-only: approve a pending registration so the user can log in."""
    admin = require_admin(authorization, db)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    user.is_active = True
    db.commit()
    db.refresh(user)

    log_activity(
        db, "User Approved", "User",
        f"User {user.name} was approved by {admin.name}.",
        admin.name, user.id,
        entity_name=user.name,
    )
    return user


@router.post("/{user_id}/reject")
def reject_user(
    user_id: int,
    authorization: str = Header(default=None),
    db: Session = Depends(get_db),
):
    """Admin-only: reject a pending registration, removing it entirely."""
    admin = require_admin(authorization, db)
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
        
    # Check if user is active
    if manager.is_user_active(user.id):
        request_id = str(uuid.uuid4())
        event = asyncio.Event()
        manager.pending_login_events[request_id] = event
        
        # Send alert
        await manager.notify_user(user.id, {
            "type": "LOGIN_ATTEMPT",
            "request_id": request_id,
            "message": "Someone is trying to log in to your account. Is this you?"
        })
        
        # Wait for approval (timeout after 60 seconds)
        try:
            await asyncio.wait_for(event.wait(), timeout=60.0)
            approved = manager.pending_login_results.get(request_id, False)
        except asyncio.TimeoutError:
            approved = False
        finally:
            manager.pending_login_events.pop(request_id, None)
            manager.pending_login_results.pop(request_id, None)
            
        if not approved:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Login denied by the active user.",
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
    return Token(access_token=token, user=UserOut.model_validate(user))

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

    log_activity(
        db, "User Logged Out", "User",
        f"User {user.name} logged out.",
        user.name, user.id,
        entity_name=user.name,
    )
    return {"message": "Logged out successfully."}


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
def active_sessions(db: Session = Depends(get_db)):
    return (
        db.query(UserSession)
        .filter(UserSession.is_active == True)
        .order_by(UserSession.login_at.desc())
        .all()
    )


@router.get("/recent-logins", response_model=List[UserSessionOut])
def recent_logins(db: Session = Depends(get_db)):
    """Returns the most recent 20 logins across all users."""
    return (
        db.query(UserSession)
        .order_by(UserSession.login_at.desc())
        .limit(20)
        .all()
    )


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)):
    return db.query(User).filter(User.is_active == True).all()


@router.put("/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db)):
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


@router.post("/reset-password")
def reset_password(payload: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User with this email not found."
        )

    user.hashed_password = hash_password(payload.password)
    db.commit()
    log_activity(
        db, "Password Reset", "User",
        f"User {user.name} reset their password.",
        user.name, user.id,
        entity_name=user.name,
    )
    return {"message": "Password updated successfully."}
