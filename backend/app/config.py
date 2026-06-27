from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    backend_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "http://localhost:3000"

    database_url: str = "postgresql+asyncpg://mira:mira@localhost:5432/mira_dev"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    llm_provider: Literal["groq", "anthropic"] = "groq"
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"

    sarvam_api_key: str | None = None
    sarvam_stt_model: str = "saaras:v3"
    sarvam_tts_model: str = "bulbul:v2"
    sarvam_tts_speaker: str = "anushka"

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
