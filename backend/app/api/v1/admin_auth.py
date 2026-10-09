"""Email one-time-code login for the internal /admin panel. See
app/auth/admin.py for the token model and why this is separate from Clerk."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.admin import (
    admin_auth_configured,
    codes_match,
    generate_code,
    hash_code,
    is_admin_email,
    issue_admin_token,
    require_admin,
)
from app.config import settings
from app.database import get_db
from app.integrations import email_client
from app.integrations.email_templates import build_admin_login_code_email_html
from app.models.admin import AdminLoginCode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/auth", tags=["admin"])

MAX_CODES_PER_WINDOW = 5
CODE_WINDOW_MINUTES = 15
MAX_ATTEMPTS_PER_CODE = 5

# Same response whether or not the address is on the allowlist, so this
# endpoint can't be used to discover which emails are admins.
_GENERIC_SENT = {"status": "sent", "detail": "If this email is authorized, a code has been sent."}


class RequestCodeIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class VerifyCodeIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class AdminSessionOut(BaseModel):
    token: str
    email: str
    expires_at: datetime


def _ensure_configured() -> None:
    if not admin_auth_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Admin login is not configured")


@router.post("/request-code")
async def request_code(body: RequestCodeIn, db: AsyncSession = Depends(get_db)) -> dict:
    _ensure_configured()
    email = body.email.strip().lower()
    if not is_admin_email(email):
        logger.warning("admin_login_code_requested_for_non_admin")
        return _GENERIC_SENT

    now = datetime.now(timezone.utc)
    recent = await db.scalar(
        select(func.count())
        .select_from(AdminLoginCode)
        .where(AdminLoginCode.email == email, AdminLoginCode.created_at >= now - timedelta(minutes=CODE_WINDOW_MINUTES))
    )
    if (recent or 0) >= MAX_CODES_PER_WINDOW:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many codes requested. Try again later.")

    # Only the newest code is ever valid -- retire any still-open ones.
    await db.execute(
        update(AdminLoginCode)
        .where(AdminLoginCode.email == email, AdminLoginCode.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    code = generate_code()
    db.add(
        AdminLoginCode(
            email=email,
            code_hash=hash_code(email, code),
            expires_at=now + timedelta(minutes=settings.admin_otp_ttl_minutes),
        )
    )
    await db.commit()

    try:
        result = await email_client.send_email(
            to=email,
            subject=f"Your Mira admin code: {code}",
            body=f"Your Mira admin sign-in code is {code}. It expires in {settings.admin_otp_ttl_minutes} minutes.",
            html_body=build_admin_login_code_email_html(code, settings.admin_otp_ttl_minutes),
        )
    except Exception:
        logger.exception("admin_login_code_email_failed")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not send the code email")
    if result.get("status") != "sent":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email delivery (Resend) is not configured on this server",
        )
    return _GENERIC_SENT


@router.post("/verify-code", response_model=AdminSessionOut)
async def verify_code(body: VerifyCodeIn, db: AsyncSession = Depends(get_db)) -> AdminSessionOut:
    _ensure_configured()
    email = body.email.strip().lower()
    invalid = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired code")
    if not is_admin_email(email):
        raise invalid

    now = datetime.now(timezone.utc)
    row = await db.scalar(
        select(AdminLoginCode)
        .where(
            AdminLoginCode.email == email,
            AdminLoginCode.consumed_at.is_(None),
            AdminLoginCode.expires_at > now,
        )
        .order_by(AdminLoginCode.created_at.desc())
        .limit(1)
    )
    if row is None or row.attempts >= MAX_ATTEMPTS_PER_CODE:
        raise invalid
    if not codes_match(email, body.code, row.code_hash):
        row.attempts += 1
        if row.attempts >= MAX_ATTEMPTS_PER_CODE:
            row.consumed_at = now
        await db.commit()
        raise invalid

    row.consumed_at = now
    await db.commit()
    token, expires_at = issue_admin_token(email)
    logger.info("admin_login_succeeded")
    return AdminSessionOut(token=token, email=email, expires_at=expires_at)


@router.get("/me")
async def me(email: str = Depends(require_admin)) -> dict:
    return {"email": email}
