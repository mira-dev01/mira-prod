import base64
import logging
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.integrations.clerk_client import ClerkError, fetch_user_email
from app.models.user import User

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


def _clerk_jwks_url() -> str:
    # Clerk's publishable key is base64("<frontend-api-domain>$") -- decoding
    # it gives this Clerk instance's JWKS endpoint without a separately
    # hardcoded/configured domain.
    if not settings.clerk_publishable_key:
        raise RuntimeError("CLERK_PUBLISHABLE_KEY is not configured")
    key_body = settings.clerk_publishable_key.split("_", 2)[-1]
    padded = key_body + "=" * (-len(key_body) % 4)
    domain = base64.b64decode(padded).decode().rstrip("$")
    return f"https://{domain}/.well-known/jwks.json"


@lru_cache
def _jwk_client() -> jwt.PyJWKClient:
    # PyJWKClient caches keys internally and only re-fetches the JWKS on a
    # cache miss (e.g. Clerk rotating signing keys) -- one client, reused
    # for the life of the process, not re-created per request.
    return jwt.PyJWKClient(_clerk_jwks_url())


async def _resolve_local_user(db: AsyncSession, clerk_user_id: str) -> User | None:
    """Links a Clerk identity to a local User row, in order:
    1. Already linked (clerk_user_id matches) -- the common case, no Clerk
       API call.
    2. Migration path: an existing row (e.g. Pause Projects, demo@mira.ai,
       created under the old bcrypt/JWT auth) whose email matches this
       Clerk user's email -- backfill clerk_user_id onto it so all their
       existing properties/leads/calls stay attached to the same User.id.
    3. Genuinely new signup -- create a fresh User row.
    Step 2/3 both require the user's email, which Clerk's default session
    token doesn't carry -- fetched once via the Backend API, never again
    once clerk_user_id is stored.
    """
    user = await db.scalar(select(User).where(User.clerk_user_id == clerk_user_id))
    if user is not None:
        return user

    try:
        email = await fetch_user_email(clerk_user_id)
    except ClerkError:
        logger.exception("Failed to fetch Clerk user email for %s", clerk_user_id)
        return None
    if email is None:
        return None

    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None:
        existing.clerk_user_id = clerk_user_id
        await db.commit()
        await db.refresh(existing)
        return existing

    user = User(email=email, clerk_user_id=clerk_user_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    try:
        signing_key = _jwk_client().get_signing_key_from_jwt(credentials.credentials)
        payload = jwt.decode(credentials.credentials, signing_key.key, algorithms=["RS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    clerk_user_id = payload.get("sub")
    if not clerk_user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    user = await _resolve_local_user(db, clerk_user_id)
    if user is None or user.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")

    # Attached for handlers that need it (currently just the "Talk to Mira"
    # gate, surfaced via GET /auth/me) -- compared directly against the
    # token's active-org id, no extra Clerk API call. Absent entirely if the
    # user has no active organization selected in their Clerk session.
    #
    # Clerk's newer "v2" session tokens carry the active org as a nested
    # `o.id` claim instead of the legacy top-level `org_id` (a compact
    # format introduced to shrink token size) -- check both, since which
    # shape a given instance issues isn't something this app controls.
    active_org_id = payload.get("org_id") or (payload.get("o") or {}).get("id")
    user.is_internal_org = bool(settings.clerk_dev_org_id) and active_org_id == settings.clerk_dev_org_id

    return user
