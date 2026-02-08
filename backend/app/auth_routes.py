from fastapi import APIRouter, Depends, Form, HTTPException

from auth import (
    user_count,
    get_user_by_username,
    create_user,
    verify_password,
    create_access_token,
    get_current_user,
    list_users,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/status")
def auth_status():
    """Returns whether initial setup is needed (no users exist yet)."""
    return {"setup_required": user_count() == 0}


@router.post("/register")
def register(username: str = Form(...), password: str = Form(...)):
    """Register the first user. Only works when no users exist."""
    if user_count() > 0:
        raise HTTPException(
            status_code=403,
            detail="Initial setup already completed. Use /auth/register-user instead.",
        )
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
    if get_user_by_username(username):
        raise HTTPException(status_code=409, detail="Username already taken")

    user = create_user(username, password)
    token = create_access_token(username)
    return {"token": token, "username": user["username"]}


@router.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    """Authenticate and return JWT."""
    user = get_user_by_username(username)
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(username)
    return {"token": token, "username": user["username"]}


@router.post("/register-user")
def register_user(
    username: str = Form(...),
    password: str = Form(...),
    current_user: dict = Depends(get_current_user),
):
    """Create additional users (authenticated only)."""
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
    if get_user_by_username(username):
        raise HTTPException(status_code=409, detail="Username already taken")
    create_user(username, password)
    return {"status": "ok", "username": username}


@router.get("/users")
def get_users(current_user: dict = Depends(get_current_user)):
    """List all users (authenticated only)."""
    return {"users": list_users()}


@router.get("/me")
def get_me(current_user: dict = Depends(get_current_user)):
    """Verify token validity and return current user info."""
    return {"username": current_user["username"]}
