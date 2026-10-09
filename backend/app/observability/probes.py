"""Active synthetic checks -- free account/status endpoints only, never a
billable request (no TTS/STT/LLM generation, no SearchApi search). Services
with no free endpoint (Sarvam, Bright Data) are monitored from real traffic
plus their credit balance instead.

Run every 60s by app/services/health_monitor_service.py; the slow ones
(quota-sensitive admin APIs) every SLOW_EVERY cycles. Groq/OpenRouter model
health comes from app/main.py's existing _check_llm_health (record_llm_health
below) and Postgres from its existing keep-alive ping -- not re-probed here.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.observability import health

PROBE_TIMEOUT_SECONDS = 10.0
SLOW_EVERY = 5  # cycles -> every 5 minutes

ProbeResult = tuple[str, str | None, dict | None]  # (ok|degraded|down, error, detail)


async def _get(url: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
        return await client.get(url, **kwargs)


def _http_result(response: httpx.Response, name: str) -> ProbeResult:
    if response.status_code in (401, 403):
        return "down", f"{name} rejected our credentials (HTTP {response.status_code})", None
    if response.status_code >= 500 or response.status_code == 429:
        return "down", f"{name} returned HTTP {response.status_code}", None
    return "ok", None, None


async def probe_redis() -> ProbeResult:
    from app.integrations import redis_client

    client = redis_client.get_client()
    if client is None:
        return "down", "REDIS_URL set but no client", None
    await client.ping()
    return "ok", None, None


async def probe_exotel() -> ProbeResult:
    response = await _get(
        f"https://{settings.exotel_subdomain}/v1/Accounts/{settings.exotel_sid}.json",
        auth=(settings.exotel_api_key, settings.exotel_api_token),
    )
    return _http_result(response, "Exotel")


async def probe_twilio() -> ProbeResult:
    response = await _get(
        f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}.json",
        auth=(settings.twilio_account_sid, settings.twilio_auth_token),
    )
    result = _http_result(response, "Twilio")
    if result[0] != "ok":
        return result
    account_status = (response.json() or {}).get("status")
    if account_status and account_status != "active":
        return "down", f"Twilio account is {account_status}", {"account_status": account_status}
    return "ok", None, {"account_status": account_status}


async def probe_resend() -> ProbeResult:
    response = await _get("https://api.resend.com/domains", headers={"Authorization": f"Bearer {settings.resend_api_key}"})
    if response.status_code == 401 and "restricted" in response.text.lower():
        # A send-only key can't list domains -- that's fine, real sends are
        # still tracked passively.
        return "ok", None, {"note": "Send-only API key; monitored from real sends."}
    result = _http_result(response, "Resend")
    if result[0] != "ok":
        return result
    sender_domain = (settings.resend_from_email or "").rpartition("@")[2].lower()
    domains = (response.json() or {}).get("data") or []
    match = next((d for d in domains if (d.get("name") or "").lower() == sender_domain), None)
    if match is None:
        return "degraded", f"Sender domain {sender_domain} isn't set up in Resend -- emails won't deliver", None
    if match.get("status") != "verified":
        return "degraded", f"Sender domain {sender_domain} is '{match.get('status')}' in Resend, not verified", None
    return "ok", None, {"sender_domain": sender_domain, "domain_status": "verified"}


async def probe_clerk() -> ProbeResult:
    response = await _get("https://api.clerk.com/v1/jwks", headers={"Authorization": f"Bearer {settings.clerk_secret_key}"})
    return _http_result(response, "Clerk")


async def probe_anthropic() -> ProbeResult:
    response = await _get(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01"},
    )
    return _http_result(response, "Anthropic")


async def probe_openrouter() -> ProbeResult:
    response = await _get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {settings.openrouter_api_key}"})
    return _http_result(response, "OpenRouter")


async def probe_turn() -> ProbeResult:
    url = settings.turn_url_tls or settings.turn_url
    # turn:host:port?transport=... -- urlparse needs a // to split netloc.
    parsed = urlparse(url.replace(":", "://", 1))
    host = parsed.hostname
    port = parsed.port or (443 if url.startswith("turns:") else 3478)
    _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5.0)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return "ok", None, {"host": host, "port": port, "check": "tcp_connect"}


async def _billing_probe(fetcher_name: str, label: str) -> ProbeResult:
    from app.integrations import billing_clients

    result = await billing_clients.LIVE_BALANCE_FETCHERS[fetcher_name]()
    if result.get("status") == "error":
        return "down", f"{label}: {result.get('detail')}", None
    return "ok", None, None


async def probe_searchapi() -> ProbeResult:
    return await _billing_probe("searchapi", "SearchApi account check failed")


async def probe_cloudinary() -> ProbeResult:
    return await _billing_probe("cloudinary", "Cloudinary admin API check failed")


# (service, probe, is_configured, slow)
PROBES: list[tuple[str, Callable[[], Awaitable[ProbeResult]], Callable[[], bool], bool]] = [
    ("redis", probe_redis, lambda: bool(settings.redis_url), False),
    ("exotel", probe_exotel, lambda: bool(settings.exotel_api_key and settings.exotel_sid), False),
    ("twilio", probe_twilio, lambda: bool(settings.twilio_account_sid and settings.twilio_auth_token), False),
    ("resend", probe_resend, lambda: bool(settings.resend_api_key), False),
    ("clerk", probe_clerk, lambda: bool(settings.clerk_secret_key), False),
    ("anthropic", probe_anthropic, lambda: bool(settings.anthropic_api_key), False),
    ("openrouter", probe_openrouter, lambda: bool(settings.openrouter_api_key), False),
    ("turn", probe_turn, lambda: bool(settings.turn_url or settings.turn_url_tls), False),
    ("searchapi", probe_searchapi, lambda: bool(settings.searchapi_api_key), True),
    ("cloudinary", probe_cloudinary, lambda: bool(settings.cloudinary_cloud_name and settings.cloudinary_api_key), True),
]


async def _run_one(service: str, probe: Callable[[], Awaitable[ProbeResult]]) -> None:
    started = time.monotonic()
    try:
        with health.suppress_passive():
            status, error, detail = await asyncio.wait_for(probe(), timeout=PROBE_TIMEOUT_SECONDS + 2)
    except asyncio.TimeoutError:
        status, error, detail = "down", f"probe timed out after {PROBE_TIMEOUT_SECONDS:.0f}s", None
    except Exception as exc:
        status, error, detail = "down", f"{type(exc).__name__}: {exc}", None
    health.record_probe(service, status, latency_ms=(time.monotonic() - started) * 1000, error=error, detail=detail)


async def run_probes(cycle: int) -> None:
    """Never raises; probes run concurrently so one slow provider can't delay
    the rest."""
    jobs = [
        _run_one(service, probe)
        for service, probe, configured, slow in PROBES
        if configured() and (not slow or cycle % SLOW_EVERY == 0)
    ]
    await asyncio.gather(*jobs, return_exceptions=True)


def record_llm_health(llm_health: dict[str, dict]) -> None:
    """Folds app.main.llm_health (per-model results of the existing 60s
    1-token check) into one Groq state: all models fine = up, primary down
    but a fallback serving = degraded (calls still work, on a weaker/backup
    model), every model down = down."""
    try:
        groq_models = [m for m in settings.groq_models if m in llm_health]
        if groq_models:
            results = [llm_health[m] for m in groq_models]
            ok_models = [m for m, r in zip(groq_models, results) if r.get("ok")]
            detail = {
                "models": [
                    {"model": m, "ok": r.get("ok"), "latency_s": r.get("latency_s"), "error": r.get("error"), "checked_at": r.get("checked_at")}
                    for m, r in zip(groq_models, results)
                ]
            }
            latency = next((r.get("latency_s") for r in results if r.get("ok")), None)
            if not ok_models:
                status, error = "down", f"Every Groq model failed: {results[0].get('error')}"
            elif not results[0].get("ok"):
                status, error = "degraded", f"Primary model {groq_models[0]} failing ({results[0].get('error')}); serving from {ok_models[0]}"
            else:
                status, error = "ok", None
            health.record_probe("groq", status, latency_ms=latency * 1000 if latency else None, error=error, detail=detail)
        for key, result in llm_health.items():
            if key.startswith("openrouter/"):
                health.record_probe(
                    "openrouter",
                    "ok" if result.get("ok") else "down",
                    latency_ms=result["latency_s"] * 1000 if result.get("latency_s") else None,
                    error=result.get("error"),
                )
    except Exception:  # pragma: no cover
        pass
