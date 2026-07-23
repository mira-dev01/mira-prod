import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import generate_refresh_token, hash_refresh_token
from app.config import settings
from app.models.refresh_token import RefreshToken


async def issue_refresh_token(db: AsyncSession, user_id: uuid.UUID) -> str:
    """Creates a new refresh_tokens row and returns the raw token to set on
    the HttpOnly cookie. Called on register/login/register-host and on every
    successful rotation in rotate_refresh_token."""
    raw, token_hash = generate_refresh_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    db.add(RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at))
    await db.commit()
    return raw


async def rotate_refresh_token(db: AsyncSession, raw_token: str) -> tuple[uuid.UUID, str] | None:
    """Validates the presented refresh token and rotates it: the old row is
    marked revoked + replaced_by, a new row is created, and (user_id,
    new_raw_token) is returned. Returns None if the token is missing,
    expired, or already revoked.

    Reuse of an already-rotated token (replaced_by_id set) revokes every
    other still-live token for that user -- the only way a legitimate
    rotation chain produces a second use of an old token is if it was
    stolen and the thief and the real client both used it, so the whole
    chain is treated as compromised rather than just rejecting the one
    request.
    """
    token_hash = hash_refresh_token(raw_token)
    existing = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if existing is None:
        return None

    now = datetime.now(timezone.utc)
    if existing.replaced_by_id is not None:
        await _revoke_all_for_user(db, existing.user_id)
        return None

    if existing.revoked_at is not None or existing.expires_at < now:
        return None

    new_raw = await issue_refresh_token(db, existing.user_id)
    new_token = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(new_raw))
    )
    existing.revoked_at = now
    existing.replaced_by_id = new_token.id
    await db.commit()
    return existing.user_id, new_raw


async def revoke_refresh_token(db: AsyncSession, raw_token: str) -> None:
    """Best-effort revoke on logout -- a missing/already-revoked token is a
    no-op, not an error, since logout should always succeed client-side
    regardless of server-side token state."""
    token_hash = hash_refresh_token(raw_token)
    existing = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if existing is not None and existing.revoked_at is None:
        existing.revoked_at = datetime.now(timezone.utc)
        await db.commit()


async def _revoke_all_for_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    result = await db.scalars(
        select(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
    )
    for token in result:
        token.revoked_at = now
    await db.commit()
