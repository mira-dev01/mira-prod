import asyncio
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


def _is_internal_org(environment: str, clerk_dev_org_id: str | None, active_org_id: str | None) -> bool:
    """Whether to grant the handful of internal-team-only affordances
    (currently just the "Talk to Mira" sidebar button). Pulled out of
    get_current_user as a pure function so it's unit-testable directly --
    get_current_user's own test coverage (tests/test_auth.py) deliberately
    overrides the dependency entirely rather than exercising a real JWT, so
    this logic would otherwise have no test path at all.

    Outside production, every host profile IS a test profile already -- the
    org-scoping only earns its keep in production, where it keeps the live
    voice-test feature restricted to Mira's own internal Clerk org rather
    than every real host. Confirmed live 2026-10-09: the previous
    unconditional version of this check hid "Talk to Mira" from every
    dev-environment host profile that wasn't a member of CLERK_DEV_ORG_ID,
    which defeated the point of a dev environment being free to test in."""
    if environment.lower() == "production":
        return bool(clerk_dev_org_id) and active_org_id == clerk_dev_org_id
    return True


def _clerk_frontend_domain() -> str:
    # Clerk's publishable key is base64("<frontend-api-domain>$") -- decoding
    # it gives this Clerk instance's frontend API domain without a separately
    # hardcoded/configured value. Both the JWKS endpoint and the token's
    # issuer ("iss") are derived from it.
    if not settings.clerk_publishable_key:
        raise RuntimeError("CLERK_PUBLISHABLE_KEY is not configured")
    key_body = settings.clerk_publishable_key.split("_", 2)[-1]
    padded = key_body + "=" * (-len(key_body) % 4)
    return base64.b64decode(padded).decode().rstrip("$")


def _clerk_jwks_url() -> str:
    return f"https://{_clerk_frontend_domain()}/.well-known/jwks.json"


def _clerk_issuer() -> str:
    return f"https://{_clerk_frontend_domain()}"


@lru_cache
def _jwk_client() -> jwt.PyJWKClient:
    # PyJWKClient caches keys internally and only re-fetches the JWKS on a
    # cache miss (e.g. Clerk rotating signing keys) -- one client, reused
    # for the life of the process, not re-created per request. The explicit
    # timeout bounds the blocking fetch (which runs in a worker thread, see
    # _verify_session_token) so a slow Clerk can't pin a thread for the
    # library's 30s default.
    return jwt.PyJWKClient(_clerk_jwks_url(), timeout=5)


def _allowed_authorized_parties() -> set[str]:
    return {origin.rstrip("/") for origin in settings.cors_allowed_origins if origin}


def _verify_session_token(token: str) -> dict:
    """Verifies a Clerk session JWT per Clerk's manual-verification checklist
    (clerk.com/docs/backend-requests/manual-jwt) and returns its claims.
    Raises jwt.PyJWTError on ANY failure; callers map that to a 401.

    Checks, each of which closes a distinct token-substitution attack:
    - signature against Clerk's JWKS, RS256 only (the algorithm list is
      pinned so an attacker can't downgrade to "none" or to HS256 keyed by
      the public key);
    - `exp`, `iat`, `sub` are REQUIRED claims -- a validly-signed token with
      no expiry would otherwise be accepted forever; `nbf` is checked when
      present, with a few seconds of clock-skew leeway;
    - `iss` must be THIS Clerk instance -- a token signed by a different
      Clerk instance's keys can't be replayed here;
    - `azp` (the origin that requested the token), when present, must be one
      of our own frontend origins -- Clerk's guard against a token minted
      for another site's session being replayed against this API. Origins
      come from the same list CORS already trusts, so any origin the browser
      can legitimately call us from is accepted and nothing else.
    Blocking (JWKS fetch on a cache miss) -- always call via
    asyncio.to_thread from async code.
    """
    signing_key = _jwk_client().get_signing_key_from_jwt(token)
    payload = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=_clerk_issuer(),
        leeway=5,
        options={"require": ["exp", "iat", "sub"]},
    )
    azp = payload.get("azp")
    if azp is not None and str(azp).rstrip("/") not in _allowed_authorized_parties():
        raise jwt.InvalidTokenError("azp is not an authorized party")
    return payload


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
        # Only an UNLINKED legacy row may be claimed by email. A row already
        # bound to a different Clerk identity must never be re-pointed here:
        # that would let anyone who controls an address-alike Clerk account
        # take over another host's data. (fetch_user_email also only returns
        # an address Clerk has VERIFIED, so owning the email is proven.)
        if existing.clerk_user_id is not None and existing.clerk_user_id != clerk_user_id:
            logger.warning(
                "Refusing to relink user %s already bound to a different Clerk identity", existing.id
            )
            return None
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
        # Off the event loop: the JWKS lookup is a blocking HTTP request on
        # a key-cache miss, and this loop is also carrying live call audio.
        payload = await asyncio.to_thread(_verify_session_token, credentials.credentials)
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
    user.is_internal_org = _is_internal_org(settings.environment, settings.clerk_dev_org_id, active_org_id)

    return user
