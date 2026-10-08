"""Live account balance / plan-usage lookups for the internal admin panel's
Balances view. Read-only, admin-only, never on a call path.

Every function returns a normalized BalanceResult dict and never raises:
status is "ok", "not_configured" (credential missing -- nothing to call) or
"error" (the provider call failed; `detail` says why). Field names in some
providers' responses aren't contractually documented, so parsing is
defensive and the raw top-level keys are surfaced in `detail` when an
expected field is missing, instead of silently showing a wrong number.

Results are cached (Redis cache module, fails open to an in-process cache)
for CACHE_TTL_SECONDS so refreshing the admin page doesn't hammer provider
APIs or eat into small plan quotas (SearchApi in particular).
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.integrations import redis_client
from app.observability import health

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300
_TIMEOUT = 10.0
_local_cache: dict[str, tuple[float, dict]] = {}


def _result(service: str, status: str, **fields: Any) -> dict:
    return {
        "service": service,
        "status": status,
        "balance": fields.get("balance"),
        "currency": fields.get("currency"),
        "used": fields.get("used"),
        "limit": fields.get("limit"),
        "unit": fields.get("unit"),
        "detail": fields.get("detail"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def twilio_balance() -> dict:
    """GET /2010-04-01/Accounts/{sid}/Balance.json -- same account SID/auth
    token the WhatsApp/Voice integrations already use; no new credential."""
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        return _result("twilio", "not_configured", detail="TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Balance.json",
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        )
    response.raise_for_status()
    data = response.json()
    return _result("twilio", "ok", balance=_num(data.get("balance")), currency=data.get("currency"))


async def openrouter_credits() -> dict:
    """GET https://openrouter.ai/api/v1/credits -- remaining = total_credits -
    total_usage (USD)."""
    if not settings.openrouter_api_key:
        return _result("openrouter", "not_configured", detail="OPENROUTER_API_KEY not set")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            "https://openrouter.ai/api/v1/credits",
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
        )
    response.raise_for_status()
    data = response.json().get("data") or {}
    total, used = _num(data.get("total_credits")), _num(data.get("total_usage"))
    if total is None:
        return _result("openrouter", "error", detail=f"Unexpected response keys: {sorted(data)}")
    return _result("openrouter", "ok", balance=round(total - (used or 0), 4), used=used, limit=total, currency="USD")


async def searchapi_account() -> dict:
    """GET https://www.searchapi.io/api/v1/me?api_key=... -- the account /
    plan endpoint. Does not consume a search credit."""
    if not settings.searchapi_api_key:
        return _result("searchapi", "not_configured", detail="SEARCHAPI_API_KEY not set")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get("https://www.searchapi.io/api/v1/me", params={"api_key": settings.searchapi_api_key})
    response.raise_for_status()
    data = response.json()
    account = data.get("account") if isinstance(data.get("account"), dict) else data
    remaining = _num(account.get("remaining_credits"))
    used = _num(account.get("current_month_usage"))
    limit = _num(account.get("monthly_allowance"))
    if remaining is None and limit is not None and used is not None:
        remaining = limit - used
    if remaining is None and used is None:
        return _result("searchapi", "error", detail=f"Unexpected response keys: {sorted(account)}")
    return _result("searchapi", "ok", balance=remaining, used=used, limit=limit, unit="searches")


async def brightdata_balance() -> dict:
    """GET https://api.brightdata.com/customer/balance -- needs an API key
    with account/billing read permission (a key scoped only to the scraper
    dataset may get 401/403 here)."""
    if not settings.bright_data_api_key:
        return _result("brightdata", "not_configured", detail="BRIGHT_DATA_API_KEY not set")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            "https://api.brightdata.com/customer/balance",
            headers={"Authorization": f"Bearer {settings.bright_data_api_key}"},
        )
    if response.status_code in (401, 403):
        return _result("brightdata", "error", detail="API key lacks billing permission -- see admin setup notes")
    response.raise_for_status()
    data = response.json()
    balance = _num(data.get("balance"))
    if balance is None:
        return _result("brightdata", "error", detail=f"Unexpected response keys: {sorted(data)}")
    pending = _num(data.get("pending_costs"))
    return _result(
        "brightdata",
        "ok",
        balance=round(balance - (pending or 0), 4),
        used=pending,
        currency="USD",
        detail="balance minus pending costs" if pending else None,
    )


async def cloudinary_usage() -> dict:
    """GET https://api.cloudinary.com/v1_1/{cloud}/usage (Admin API, basic
    auth key:secret) -- plan credits used vs limit."""
    if not (settings.cloudinary_cloud_name and settings.cloudinary_api_key and settings.cloudinary_api_secret):
        return _result("cloudinary", "not_configured", detail="CLOUDINARY_* not set")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"https://api.cloudinary.com/v1_1/{settings.cloudinary_cloud_name}/usage",
            auth=(settings.cloudinary_api_key, settings.cloudinary_api_secret),
        )
    response.raise_for_status()
    data = response.json()
    credits = data.get("credits") if isinstance(data.get("credits"), dict) else {}
    used, limit = _num(credits.get("usage")), _num(credits.get("limit"))
    if used is None:
        return _result("cloudinary", "error", detail=f"Unexpected response keys: {sorted(data)}")
    return _result(
        "cloudinary",
        "ok",
        balance=round(limit - used, 4) if limit is not None else None,
        used=used,
        limit=limit,
        unit="credits",
        detail=data.get("plan"),
    )


async def neon_usage() -> dict:
    """GET https://console.neon.tech/api/v2/projects/{project_id} -- the
    project's consumption for the current billing period (compute hours,
    storage). Neon exposes no prepaid balance; this is plan usage."""
    if not (settings.neon_api_key and settings.neon_project_id):
        return _result("neon", "not_configured", detail="NEON_API_KEY / NEON_PROJECT_ID not set")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"https://console.neon.tech/api/v2/projects/{settings.neon_project_id}",
            headers={"Authorization": f"Bearer {settings.neon_api_key}", "Accept": "application/json"},
        )
    response.raise_for_status()
    project = response.json().get("project") or {}
    compute_seconds = _num(project.get("compute_time_seconds"))
    storage_bytes = _num(project.get("synthetic_storage_size"))
    if compute_seconds is None and storage_bytes is None:
        return _result("neon", "error", detail=f"Unexpected response keys: {sorted(project)}")
    parts = []
    if storage_bytes is not None:
        parts.append(f"storage {storage_bytes / 1024**3:.2f} GB")
    if project.get("consumption_period_start"):
        parts.append(f"period from {str(project['consumption_period_start'])[:10]}")
    return _result(
        "neon",
        "ok",
        used=round(compute_seconds / 3600, 2) if compute_seconds is not None else None,
        unit="compute hours",
        detail=", ".join(parts) or None,
    )


LIVE_BALANCE_FETCHERS = {
    "twilio": twilio_balance,
    "openrouter": openrouter_credits,
    "searchapi": searchapi_account,
    "brightdata": brightdata_balance,
    "cloudinary": cloudinary_usage,
    "neon": neon_usage,
}


async def fetch_balance(service: str, *, refresh: bool = False) -> dict:
    """Cached, never-raising wrapper around one LIVE_BALANCE_FETCHERS entry."""
    key = f"admin:balance:{service}"
    if not refresh:
        cached = await redis_client.cache_get_json(key)
        if cached is None:
            local = _local_cache.get(key)
            if local and time.monotonic() - local[0] < CACHE_TTL_SECONDS:
                cached = local[1]
        if cached is not None:
            return cached
    try:
        # Balance lookups are admin bookkeeping, not service traffic -- a key
        # without billing permission (Bright Data) must not make the service
        # itself look broken. Service health comes from real traffic + probes.
        with health.suppress_passive():
            result = await LIVE_BALANCE_FETCHERS[service]()
    except httpx.HTTPStatusError as e:
        result = _result(service, "error", detail=f"HTTP {e.response.status_code} from provider")
    except Exception as e:
        logger.exception("admin_balance_fetch_failed service=%s", service)
        result = _result(service, "error", detail=type(e).__name__)
    if result["status"] != "error":
        _local_cache[key] = (time.monotonic(), result)
        await redis_client.cache_set_json(key, result, CACHE_TTL_SECONDS)
    return result
