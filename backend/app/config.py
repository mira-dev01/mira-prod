from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    backend_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "http://localhost:3000"

    database_url: str = "postgresql+asyncpg://mira:mira@localhost:5432/mira_dev"
    redis_url: str = "redis://localhost:6379/0"

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg_driver(cls, value: str) -> str:
        # Render (and most hosts) hand out a bare postgres:// or postgresql://
        # connection string -- SQLAlchemy's async engine needs the asyncpg
        # driver named explicitly in the scheme.
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value[len("postgres://") :]
        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value[len("postgresql://") :]
        return value

    jwt_secret_key: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    llm_provider: Literal["groq", "anthropic", "openrouter"] = "groq"
    groq_api_key: str | None = None
    # llama-3.3-70b-versatile was deprecated by Groq on 2026-06-17;
    # openai/gpt-oss-120b is Groq's recommended replacement.
    groq_model: str = "openai/gpt-oss-120b"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    # OpenRouter (https://openrouter.ai) -- one account/balance, swap models
    # by changing openrouter_model alone (e.g. "openai/gpt-4.1",
    # "anthropic/claude-sonnet-4.6"), no new integration code per model.
    openrouter_api_key: str | None = None
    openrouter_model: str = "openai/gpt-4.1"

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

    ical_sync_interval_minutes: int = 15

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
