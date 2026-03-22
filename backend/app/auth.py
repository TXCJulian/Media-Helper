import hmac
import logging

import bcrypt
from fastapi import Request, Response
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired

from app.config import AUTH_ENABLED, AUTH_USERNAME, SECRET_KEY, _PASSWORD_HASH

logger = logging.getLogger(__name__)

SESSION_COOKIE = "session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days in seconds

_signer = TimestampSigner(SECRET_KEY)


def verify_login(username: str, password: str) -> bool:
    if not AUTH_ENABLED or _PASSWORD_HASH is None:
        return False
    username_ok = hmac.compare_digest(username, AUTH_USERNAME)
    password_ok = bcrypt.checkpw(password.encode("utf-8"), _PASSWORD_HASH)
    return username_ok and password_ok


def create_session_cookie(response: Response, *, secure: bool = False) -> None:
    signed = _signer.sign(AUTH_USERNAME).decode("utf-8")
    response.set_cookie(
        SESSION_COOKIE,
        signed,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)


def check_session(request: Request) -> bool:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return False
    try:
        username = _signer.unsign(cookie, max_age=SESSION_MAX_AGE).decode("utf-8")
        return hmac.compare_digest(username, AUTH_USERNAME)
    except (BadSignature, SignatureExpired):
        return False
