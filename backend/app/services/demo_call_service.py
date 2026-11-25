"""Grants, limits and the bot check for the marketing site's public "test the agent" call.

An anonymous visitor can start a live call that costs real speech and model time, so every step that lets one begin
is bounded here and none of it trusts the browser:

  * the visitor leaves a name and a number first (they are the lead);
  * a Cloudflare Turnstile check when configured (fails closed);
  * per-IP and per-number limits and a daily cap, all counted from the database so they hold across replicas;
  * a signed, short-lived, single-use token binds the call to that one request;
  * a ceiling on simultaneous calls.

The call itself runs against the sample host only, with every side effect simulated (app/voice/demo_mode.py), and is
cut off after `demo_call_max_seconds` by the pipeline, not by the page.
"""

import hashlib
import hmac
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import Request
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.demo_call import DemoCallRequest

TOKEN_TTL_SECONDS = 300
_TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_NAME_MAX = 80


class DemoCallRefused(Exception):
    """A request the demo call will not serve. `status_code` and `message` go straight to the visitor."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def clean_name(raw: str | None) -> str | None:
    name = re.sub(r"\s+", " ", (raw or "")).strip()
    if len(name) < 2 or len(name) > _NAME_MAX:
        return None
    if any(ord(ch) < 32 for ch in name) or "<" in name or ">" in name:
        return None
    return name


def clean_phone(raw: str | None) -> str | None:
    """Digits only (a leading + is kept), 8 to 15 of them: the E.164 range. Anything else is not a phone number."""
    value = (raw or "").strip()
    if not re.fullmatch(r"\+?[0-9(][0-9 ()\-.]{5,24}", value):
        return None
    digits = re.sub(r"\D", "", value)
    if not 8 <= len(digits) <= 15:
        return None
    return ("+" if value.startswith("+") else "") + digits


def client_ip(request: Request) -> str:
    """The visitor's address behind Railway's proxy: the first X-Forwarded-For hop, else the socket peer."""
    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    if first:
        return first
    return request.client.host if request.client else "unknown"


def hash_ip(ip: str) -> str:
    return hashlib.sha256(f"{settings.demo_call_secret}:{ip}".encode()).hexdigest()


def _sign(payload: str) -> str:
    return hmac.new(settings.demo_call_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(request_id: uuid.UUID, *, now: float | None = None) -> str:
    expires = int((now if now is not None else time.time()) + TOKEN_TTL_SECONDS)
    payload = f"{request_id}.{expires}"
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str | None, *, now: float | None = None) -> uuid.UUID | None:
    """The request id inside a token we issued and that has not expired, else None. Constant-time comparison."""
    if not token or not settings.demo_call_enabled:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    request_id, expires, signature = parts
    if not hmac.compare_digest(signature, _sign(f"{request_id}.{expires}")):
        return None
    try:
        if int(expires) < (now if now is not None else time.time()):
            return None
        return uuid.UUID(request_id)
    except ValueError:
        return None


async def verify_turnstile(token: str | None, ip: str) -> bool:
    """True when the bot check is switched off, or Cloudflare accepts the token. Any failure to reach Cloudflare
    refuses the visitor: an unchecked path to a paid call is worse than a visitor retrying."""
    if not settings.demo_turnstile_secret:
        return True
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                _TURNSTILE_URL,
                data={"secret": settings.demo_turnstile_secret, "response": token, "remoteip": ip},
            )
        return bool(response.json().get("success"))
    except Exception:
        logger.exception("Turnstile verification failed")
        return False


async def enforce_limits(db: AsyncSession, *, ip_hash: str, phone: str, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)

    async def count(*conditions) -> int:
        return int(await db.scalar(select(func.count()).select_from(DemoCallRequest).where(*conditions)) or 0)

    if await count(DemoCallRequest.ip_hash == ip_hash, DemoCallRequest.created_at >= now - timedelta(hours=1)) >= (
        settings.demo_call_per_ip_hour
    ):
        raise DemoCallRefused(429, "You've used your test calls for now. Book a demo and we'll walk you through it.")
    if await count(DemoCallRequest.phone == phone, DemoCallRequest.created_at >= now - timedelta(days=1)) >= (
        settings.demo_call_per_phone_day
    ):
        raise DemoCallRefused(429, "This number has already tested Mira today. Book a demo to see more.")
    if await count(DemoCallRequest.created_at >= now - timedelta(days=1)) >= settings.demo_call_daily_cap:
        raise DemoCallRefused(503, "The live test is very busy today. Book a demo and we'll show you Mira.")


class ActiveCalls:
    """The demo calls running in this process. In-process by design: it bounds simultaneous spend per replica."""

    def __init__(self) -> None:
        self._ids: set[uuid.UUID] = set()

    def try_acquire(self, request_id: uuid.UUID) -> bool:
        if request_id in self._ids or len(self._ids) >= settings.demo_call_max_concurrent:
            return False
        self._ids.add(request_id)
        return True

    def release(self, request_id: uuid.UUID) -> None:
        self._ids.discard(request_id)

    def __len__(self) -> int:
        return len(self._ids)


active_calls = ActiveCalls()
