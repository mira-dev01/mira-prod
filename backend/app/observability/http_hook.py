"""Passive health signal for every outbound HTTP call, from one place.

Nearly every external dependency here is reached over httpx -- directly
(Twilio, Resend, Exotel, SearchApi, Bright Data, Clerk, billing lookups) or
underneath an SDK (the groq/openai/anthropic SDKs all use httpx). Wrapping
httpx's send() once classifies each request by host and records it, so a
new call site is covered automatically instead of needing its own
try/except-and-record.

Observation only: the request, response and any exception pass through
unchanged; the recording itself can't raise. Not covered (no httpx): the
Sarvam STT/TTS websockets and the Cloudinary SDK -- those are recorded from
app/voice/pipeline.py and the Cloudinary probe respectively.
"""

from __future__ import annotations

import time

import httpx

from app.config import settings
from app.observability import health

_installed = False

_EXACT_HOSTS = {
    "api.groq.com": "groq",
    "openrouter.ai": "openrouter",
    "api.anthropic.com": "anthropic",
    "api.resend.com": "resend",
    "www.searchapi.io": "searchapi",
    "api.brightdata.com": "bright_data",
    "api.clerk.com": "clerk",
    "api.cloudinary.com": "cloudinary",
}
_SUFFIX_HOSTS = [
    ("twilio.com", "twilio"),
    ("exotel.com", "exotel"),
    ("exotel.in", "exotel"),
    ("clerk.accounts.dev", "clerk"),
]


def service_for_host(host: str) -> str | None:
    host = (host or "").lower()
    if host in _EXACT_HOSTS:
        return _EXACT_HOSTS[host]
    if host == (settings.exotel_subdomain or "").lower():
        return "exotel"
    for suffix, service in _SUFFIX_HOSTS:
        if host == suffix or host.endswith("." + suffix):
            return service
    return None


def classify_status(status_code: int) -> tuple[str, str | None]:
    """A provider failing (5xx) or rejecting our credentials (401/403) is a
    service problem; throttling us (429) is a warning. Any other 4xx is a problem
    with one particular request (a bad phone number, WhatsApp's 24h window,
    an unknown listing) -- the service itself is fine."""
    if status_code >= 500:
        return "error", f"http_{status_code}"
    if status_code == 429:
        # Throttled, not broken -- and for Groq the pipeline immediately
        # retries on the next model, so the call still works. Warnings make
        # a service degraded, never down.
        return "warn", "rate_limited"
    if status_code in (401, 403):
        return "error", "auth_failed"
    return "ok", None


def _observe(request: httpx.Request, started: float, response: httpx.Response | None, exc: BaseException | None) -> None:
    try:
        if health.passive_suppressed():
            return
        service = service_for_host(request.url.host)
        if service is None:
            return
        latency_ms = (time.monotonic() - started) * 1000
        op = f"{request.method} {request.url.path}"[:120]
        if exc is not None:
            kind = "timeout" if isinstance(exc, httpx.TimeoutException) else type(exc).__name__
            health.record(service, "error", latency_ms=latency_ms, error=exc, kind=kind, op=op)
            return
        outcome, kind = classify_status(response.status_code)
        if outcome == "ok":
            health.record(service, "ok", latency_ms=latency_ms, op=op)
        else:
            try:
                # Non-streamed responses are already read by send(); a
                # streamed one raises ResponseNotRead -- never force a read.
                body = response.text[:300]
            except Exception:
                body = ""
            health.record(
                service,
                outcome,
                latency_ms=latency_ms,
                error=f"HTTP {response.status_code} from {request.url.host}{request.url.path} {body}".strip(),
                kind=kind,
                op=op,
            )
    except Exception:  # pragma: no cover - observation must never break a request
        pass


def install() -> None:
    """Idempotent. Called once at app import (app/main.py)."""
    global _installed
    if _installed:
        return
    _installed = True

    original_async_send = httpx.AsyncClient.send
    original_sync_send = httpx.Client.send

    async def _async_send(self, request, *args, **kwargs):
        started = time.monotonic()
        try:
            response = await original_async_send(self, request, *args, **kwargs)
        except BaseException as exc:
            if isinstance(exc, Exception):
                _observe(request, started, None, exc)
            raise
        _observe(request, started, response, None)
        return response

    def _sync_send(self, request, *args, **kwargs):
        started = time.monotonic()
        try:
            response = original_sync_send(self, request, *args, **kwargs)
        except BaseException as exc:
            if isinstance(exc, Exception):
                _observe(request, started, None, exc)
            raise
        _observe(request, started, response, None)
        return response

    httpx.AsyncClient.send = _async_send
    httpx.Client.send = _sync_send
