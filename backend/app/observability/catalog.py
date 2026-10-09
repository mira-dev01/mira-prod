"""Every external dependency / internal subsystem the /admin health page
tracks -- one place to add a new one. The key is what callers pass to
app.observability.health.record(...); the rest is presentation and alert
policy.

`critical` = a failure here directly breaks a live guest call (urgent email
subject). Every service still alerts on DOWN regardless; critical only
changes how loudly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.config import settings

GROUP_ORDER = ["voice", "ai", "telephony", "platform", "data", "credits"]
GROUP_LABELS = {
    "voice": "Live calls",
    "ai": "AI models",
    "telephony": "Telephony & messaging",
    "platform": "Platform",
    "data": "Data & integrations",
    "credits": "Credits left",
}


@dataclass(frozen=True)
class ServiceDef:
    key: str
    label: str
    group: str
    description: str
    configured: Callable[[], bool] = field(default=lambda: True)
    critical: Callable[[], bool] = field(default=lambda: False)


def _llm_is(provider: str) -> Callable[[], bool]:
    return lambda: settings.llm_provider == provider


SERVICES: dict[str, ServiceDef] = {
    s.key: s
    for s in [
        ServiceDef(
            "voice_pipeline",
            "Voice pipeline",
            "voice",
            "Pipecat call pipeline -- a crash here ends the guest's call (MISSED_SYSTEM_FAILURE).",
            critical=lambda: True,
        ),
        ServiceDef(
            "sarvam_stt",
            "Sarvam STT",
            "voice",
            "Speech-to-text websocket. Monitored from real calls (errors, reconnects).",
            configured=lambda: bool(settings.sarvam_api_key),
            critical=lambda: True,
        ),
        ServiceDef(
            "sarvam_tts",
            "Sarvam TTS",
            "voice",
            "Text-to-speech websocket. Monitored from real calls.",
            configured=lambda: bool(settings.sarvam_api_key),
            critical=lambda: True,
        ),
        ServiceDef(
            "turn",
            "TURN relay",
            "voice",
            "Media relay for browser test calls (TCP reachability probe).",
            configured=lambda: bool(settings.turn_url or settings.turn_url_tls),
        ),
        ServiceDef(
            "groq",
            "Groq LLM",
            "ai",
            "Live-call LLM. Per-model 1-token probe every 60s + every real completion.",
            configured=lambda: bool(settings.groq_api_key),
            critical=_llm_is("groq"),
        ),
        ServiceDef(
            "openrouter",
            "OpenRouter",
            "ai",
            "Embeddings, post-call LLM and the last-resort live-call fallback.",
            configured=lambda: bool(settings.openrouter_api_key),
            critical=_llm_is("openrouter"),
        ),
        ServiceDef(
            "anthropic",
            "Anthropic",
            "ai",
            "Only used when LLM_PROVIDER=anthropic.",
            configured=lambda: bool(settings.anthropic_api_key),
            critical=_llm_is("anthropic"),
        ),
        ServiceDef(
            "exotel",
            "Exotel",
            "telephony",
            "Phone-call telephony API.",
            configured=lambda: bool(settings.exotel_api_key and settings.exotel_sid),
            critical=lambda: True,
        ),
        ServiceDef(
            "twilio",
            "Twilio (WhatsApp + Voice)",
            "telephony",
            "WhatsApp sends, busy-call recovery, Twilio Voice.",
            configured=lambda: bool(settings.twilio_account_sid and settings.twilio_auth_token),
        ),
        ServiceDef(
            "resend",
            "Resend email",
            "telephony",
            "Escalation, call-summary, admin-login and these alert emails.",
            configured=lambda: bool(settings.resend_api_key and settings.resend_from_email),
        ),
        ServiceDef(
            "postgres",
            "Postgres (Neon)",
            "platform",
            "Primary database.",
            critical=lambda: True,
        ),
        ServiceDef(
            "redis",
            "Redis",
            "platform",
            "Busy-call leases (CallCoordinator) + pricing cache.",
            configured=lambda: bool(settings.redis_url),
            critical=lambda: True,
        ),
        ServiceDef("api", "REST API", "platform", "Dashboard/webhook HTTP requests (5xx rate)."),
        ServiceDef("scheduler", "Background jobs", "platform", "APScheduler jobs (iCal sync, pricing refresh, sweeps)."),
        ServiceDef(
            "clerk",
            "Clerk auth",
            "platform",
            "Host dashboard sign-in.",
            configured=lambda: bool(settings.clerk_secret_key),
        ),
        ServiceDef("ical_sync", "iCal calendar sync", "data", "Per-property Airbnb/Booking.com calendar fetches."),
        ServiceDef(
            "searchapi",
            "SearchApi.io",
            "data",
            "Live Airbnb pricing during calls + daily comparables.",
            configured=lambda: bool(settings.searchapi_api_key),
        ),
        ServiceDef(
            "bright_data",
            "Bright Data",
            "data",
            "Airbnb listing import.",
            configured=lambda: bool(settings.bright_data_api_key),
        ),
        ServiceDef(
            "cloudinary",
            "Cloudinary",
            "data",
            "Property photo hosting.",
            configured=lambda: bool(settings.cloudinary_cloud_name and settings.cloudinary_api_key),
        ),
    ]
}

CREDITS_PREFIX = "credits:"


_dynamic_labels: dict[str, str] = {}


def credits_key(account: str) -> str:
    return f"{CREDITS_PREFIX}{account}"


def set_label(key: str, label: str) -> None:
    _dynamic_labels[key] = label


def get(key: str) -> ServiceDef:
    """Credits services are registered lazily from the Balances data (the set
    of accounts lives in admin_monitor_service.ACCOUNTS / billing_clients)."""
    found = SERVICES.get(key)
    if found is not None:
        return found
    if key.startswith(CREDITS_PREFIX):
        account = key[len(CREDITS_PREFIX):]
        label = _dynamic_labels.get(key) or account.replace("_", " ").title()
        return ServiceDef(key, label, "credits", "Remaining credit / quota, from the admin Balances data.")
    return ServiceDef(key, key.replace("_", " ").title(), "platform", "")
