from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from jose import jwt
from datetime import datetime, timedelta
from app.database import get_db
from app.models.models import User
from app.schemas.user import UserCreate, UserUpdate, UserOut, UserLogin, Token
from app.config import settings
from app.utils.helpers import log_activity

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
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    
    log_activity(db, "User Registered", "User", f"New user {user.name} registered.", user.name, user.id)
    return user


@router.post("/login", response_model=Token)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    token = create_access_token(user.id)
    log_activity(db, "User Logged In", "User", f"User {user.name} logged in.", user.name, user.id)
    return Token(access_token=token, user=UserOut.model_validate(user))


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
    log_activity(db, "User Updated", "User", f"User {user.name} was updated.", "System/Admin", user.id)
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
    log_activity(db, "Password Reset", "User", f"User {user.name} reset their password.", user.name, user.id)
    return {"message": "Password updated successfully."}
