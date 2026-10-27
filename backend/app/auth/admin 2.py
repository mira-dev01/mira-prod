"""Internal admin-panel auth -- deliberately separate from host (Clerk) auth.

A host signing in through Clerk auto-provisions a host User row (see
dependencies._resolve_local_user); admins are operators, not hosts, so they
get their own minimal identity: an emailed one-time code exchanged for a
short-lived HS256 JWT (audience ADMIN_AUDIENCE, signed with
settings.admin_jwt_secret -- never shared with any other token in the app).
The allowlist (settings.admin_email_set) is re-checked on every request, so
removing an address from ADMIN_EMAILS revokes an already-issued token.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

ADMIN_AUDIENCE = "mira-admin"
_ALGORITHM = "HS256"

_bearer = HTTPBearer(auto_error=False)


def admin_auth_configured() -> bool:
    return bool(settings.admin_jwt_secret)


def is_admin_email(email: str | None) -> bool:
    return bool(email) and email.strip().lower() in settings.admin_email_set


def generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(email: str, code: str) -> str:
    """HMAC (not a bare hash) so a leaked admin_login_codes row can't be
    brute-forced offline -- a 6-digit space is trivially enumerable."""
    key = (settings.admin_jwt_secret or "").encode()
    return hmac.new(key, f"{email.strip().lower()}:{code}".encode(), hashlib.sha256).hexdigest()


def codes_match(email: str, code: str, code_hash: str) -> bool:
    return hmac.compare_digest(hash_code(email, code), code_hash)


def issue_admin_token(email: str) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=settings.admin_session_hours)
    token = jwt.encode(
        {"sub": email.strip().lower(), "aud": ADMIN_AUDIENCE, "iat": now, "exp": expires_at},
        settings.admin_jwt_secret,
        algorithm=_ALGORITHM,
    )
    return token, expires_at


async def require_admin(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    """FastAPI dependency: returns the admin's email or raises. 404 (not
    401/403) when admin auth isn't configured at all, so an unconfigured
    deployment doesn't even advertise that an admin surface exists."""
    if not admin_auth_configured():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.admin_jwt_secret,
            algorithms=[_ALGORITHM],
            audience=ADMIN_AUDIENCE,
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired admin session")
    email = payload.get("sub")
    if not is_admin_email(email):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not an admin")
    return email
