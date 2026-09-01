import re

with open("app/routers/users.py", "r", encoding="utf-8") as f:
    content = f.read()

# Add imports
imports_to_add = """
import uuid
import asyncio
from fastapi import WebSocket, WebSocketDisconnect
from app.services.session_manager import manager
"""
content = content.replace("from app.utils.auth import get_user_id_from_token, require_admin", "from app.utils.auth import get_user_id_from_token, require_admin\n" + imports_to_add)

# Replace login endpoint
new_login = """@router.post("/login", response_model=Token)
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
async def approve_login(payload: LoginApproval, user_id: int = Depends(get_user_id_from_token)):
    request_id = payload.request_id
    if request_id in manager.pending_login_events:
        manager.pending_login_results[request_id] = (payload.action == "approve")
        manager.pending_login_events[request_id].set()
        return {"message": "Action processed."}
    return {"message": "Invalid or expired request."}

@router.websocket("/ws/auth")
async def websocket_endpoint(websocket: WebSocket):
    # Get token from query param (WebSocket doesn't support auth headers easily from browser API)
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
        
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id_str = payload.get("sub")
        if not user_id_str:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        user_id = int(user_id_str)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
        
    await manager.connect(websocket, user_id)
    try:
        while True:
            # Keep connection alive, wait for client messages if any
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)
"""

content = re.sub(r'@router\.post\("/login", response_model=Token\)\ndef login\(payload: UserLogin, db: Session = Depends\(get_db\)\):.*?return Token\(access_token=token, user=UserOut\.model_validate\(user\)\)', new_login, content, flags=re.DOTALL)

with open("app/routers/users.py", "w", encoding="utf-8") as f:
    f.write(content)
