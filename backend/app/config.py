from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    backend_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "http://localhost:3000"
    # Extra origins allowed to hit the API via CORS, comma-separated (e.g.
    # "http://localhost:3000,https://staging.example.com"). Useful for hitting
    # a deployed backend from a local frontend during development. The
    # frontend_base_url above is always allowed and doesn't need to be repeated.
    cors_extra_origins: str = ""

    @property
    def cors_allowed_origins(self) -> list[str]:
        extra = [origin.strip() for origin in self.cors_extra_origins.split(",") if origin.strip()]
        return list(dict.fromkeys([self.frontend_base_url, *extra]))

    database_url: str = "postgresql+asyncpg://mira:mira@localhost:5432/mira_dev"
    # Whether DATABASE_URL asked for TLS via ?sslmode=require or ?ssl=require
    # (e.g. Neon). Derived from the raw URL by _use_asyncpg_driver below (that
    # query param is then stripped from database_url itself, since asyncpg's
    # connect() rejects an unrecognized "sslmode"/"ssl" kwarg forwarded from
    # the URL). Consumed as connect_args={"ssl": True} in database.py /
    # alembic/env.py, where asyncpg wants a bool/SSLContext, not a
    # query-string value. Render's own internal DB URL has no such param, so
    # this correctly stays False there and TLS isn't forced on it.
    database_requires_ssl: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize_database_url(cls, data: dict) -> dict:
        value = data.get("database_url")
        if not value:
            return data
        # Render (and most hosts) hand out a bare postgres:// or postgresql://
        # connection string -- SQLAlchemy's async engine needs the asyncpg
        # driver named explicitly in the scheme.
        if value.startswith("postgres://"):
            value = "postgresql+asyncpg://" + value[len("postgres://") :]
        elif value.startswith("postgresql://"):
            value = "postgresql+asyncpg://" + value[len("postgresql://") :]

        parsed = urlsplit(value)
        params = parse_qsl(parsed.query)
        mode = next((v for k, v in params if k in ("sslmode", "ssl")), None)
        data["database_requires_ssl"] = mode is not None and mode not in ("disable", "false", "0")
        if parsed.query:
            remaining = [(k, v) for k, v in params if k not in ("sslmode", "ssl")]
            parsed = parsed._replace(query=urlencode(remaining))
            value = urlunsplit(parsed)

        data["database_url"] = value
        return data

    jwt_secret_key: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    llm_provider: Literal["groq", "anthropic", "openrouter"] = "groq"
    groq_api_key: str | None = None
    # llama-3.3-70b-versatile was deprecated by Groq on 2026-06-17;
    # openai/gpt-oss-120b is Groq's recommended replacement.
    groq_model: str = "openai/gpt-oss-120b"
    # Fallback chain tried in order when llm_provider="groq". groq_model
    # (above) stays as the configured default/first choice -- this list is
    # what _build_llm() in app/voice/pipeline.py actually walks, skipping any
    # model app/main.py's periodic health check has marked down (e.g. from a
    # 429 rate limit on gpt-oss-120b's free-tier TPM cap). All three IDs
    # confirmed live via client.models.list() against this account -- Groq
    # renames/deprecates model ids periodically (see the groq_model comment
    # above re: llama-3.3-70b-versatile), so re-check before editing this
    # list. llama-3.1-8b-instant and gpt-oss-20b are on separate rate-limit
    # pools from gpt-oss-120b, so a burst that exhausts one doesn't take down
    # the others; both also support function calling, required for our tools
    # (app/voice/tools.py) -- don't add a model here without confirming that.
    groq_models: list[str] = ["openai/gpt-oss-120b", "llama-3.1-8b-instant", "openai/gpt-oss-20b"]
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    # OpenRouter (https://openrouter.ai) -- one account/balance, swap models
    # by changing openrouter_model alone (e.g. "openai/gpt-4.1",
    # "anthropic/claude-sonnet-4.6"), no new integration code per model.
    openrouter_api_key: str | None = None
    openrouter_model: str = "openai/gpt-4.1"

    # Bright Data Web Scraper API (Airbnb dataset gd_ld7ll037kqy322v05) --
    # onboarding's "import from Airbnb" flow. Their Airbnb product takes
    # individual listing URLs, not a host-profile URL that returns every
    # listing -- confirmed against their docs and sample dataset (no
    # discovery-by-host mode exists for Airbnb, unlike some of their other
    # scrapers). The host pastes each listing URL instead of one profile
    # link. See app/integrations/bright_data_client.py.
    bright_data_api_key: str | None = None

    # SearchApi.io's Airbnb engine (https://www.searchapi.io/airbnb-api) --
    # daily comparable-listing pricing for smart_pricing_service.py, feeding
    # Property.smart_price_estimate. Free tier is a small one-time/monthly
    # request allowance (SearchApi.io doesn't publish which) -- the daily
    # refresh job makes one call per unique city, not per property, to
    # stretch it. Unset = the refresh job no-ops (same pattern as
    # bright_data_api_key).
    searchapi_api_key: str | None = None

    # Cloudinary -- re-hosts property photos scraped via Bright Data
    # (see app/integrations/cloudinary_client.py) so listing images survive
    # even if the source Airbnb listing is edited/removed, and so MIRA can
    # serve a resized/optimized version to guests later rather than hotlink
    # Airbnb's own CDN. All three required together; any missing = upload
    # skipped silently (same "don't crash, don't block" pattern as
    # bright_data_api_key/SMTP_*).
    cloudinary_cloud_name: str | None = None
    cloudinary_api_key: str | None = None
    cloudinary_api_secret: str | None = None

    sarvam_api_key: str | None = None
    sarvam_stt_model: str = "saaras:v3"
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_tts_speaker: str = "roopa"

    # TURN relay for the in-dashboard "test in browser" WebRTC feature.
    # STUN alone (just discovering each side's public address) is enough on
    # localhost, but most cloud hosts don't allow the resulting direct UDP
    # media connection through at all -- ICE then times out instead of
    # connecting. TURN relays the actual audio through a third server over
    # a connection that looks like normal outbound traffic, which works
    # around that. Optional: only used if turn_url is set; falls back to
    # STUN-only (works on localhost, not on most cloud hosts) otherwise.
    turn_url: str | None = None
    turn_username: str | None = None
    turn_credential: str | None = None

    # Mobile carrier networks frequently block or throttle plain UDP (what
    # turn_url above uses) far more aggressively than WiFi/broadband --
    # confirmed by browser test calls failing on mobile data while working
    # fine on WiFi. A TURNS-over-TCP relay on port 443 looks identical to
    # normal HTTPS traffic to any carrier NAT/firewall, so it's offered as a
    # second ICE server alongside turn_url rather than replacing it -- the
    # browser tries all candidates and uses whichever the network allows.
    # Same username/credential as turn_url (Metered and most providers issue
    # one credential pair valid across all of their relay endpoints).
    turn_url_tls: str | None = None

    @field_validator("turn_url", "turn_url_tls")
    @classmethod
    def _validate_turn_url(cls, value: str | None) -> str | None:
        if value is not None and not (value.startswith("turn:") or value.startswith("turns:")):
            raise ValueError(
                f"TURN_URL must start with 'turn:' or 'turns:', got: {value!r}"
            )
        return value

    exotel_sid: str | None = None
    exotel_api_key: str | None = None
    exotel_api_token: str | None = None
    exotel_subdomain: str = "api.exotel.com"
    exotel_webhook_token: str = "change-me"
    exotel_gateway_ip: str | None = None
    exotel_gateway_port: int = 5070

    # Email escalation summaries -- interim stand-in for a WhatsApp Business
    # API host notification (that needs Meta business verification + Exotel
    # KYC approval, no instant sandbox exists unlike Twilio's). Any SMTP
    # provider works (Gmail app password, Zoho, Amazon SES SMTP, ...); unset
    # = escalate_to_host still creates the in-app notification, just skips
    # the email (see app/integrations/email_client.py).
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_use_tls: bool = True

    ical_sync_interval_minutes: int = 15

    # Experimental (shagun branch only, not deployed to main/production).
    # "vad_fixed" is today's proven SpeechTimeoutUserTurnStopStrategy
    # (user_speech_timeout=0.9s) in app/voice/pipeline.py, unchanged by
    # default. "hybrid_experimental" swaps in
    # app/voice/turn_strategies.HybridCompletenessUserTurnStopStrategy, which
    # extends the wait (up to a hard cap) when the transcript looks
    # mid-sentence, instead of firing at a fixed timeout. Local-only via
    # TURN_DETECTION_STRATEGY in backend/.env -- deliberately not added to
    # render.yaml or CLAUDE.md's env var table, since those are shared
    # production config.
    turn_detection_strategy: Literal["vad_fixed", "hybrid_experimental"] = "vad_fixed"

    default_cleaning_fee_inr: int = 800
    default_tax_percent: float = 12.0
    weekend_surge_multiplier: float = 1.2

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
