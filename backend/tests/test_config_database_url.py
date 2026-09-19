"""Confirms Settings._normalize_database_url strips every query param
asyncpg's own connect() doesn't accept as a kwarg -- see that validator's
own comment and app.config._ASYNCPG_UNSUPPORTED_QUERY_PARAMS for the live
incident (a Neon DATABASE_URL's ?sslmode=require&channel_binding=require
crashed every alembic upgrade/app startup with TypeError: connect() got an
unexpected keyword argument 'channel_binding') this guards against.
"""

from app.config import Settings


def test_channel_binding_is_stripped_from_neon_style_url():
    settings = Settings(
        database_url="postgresql://user:pass@ep-example.us-east-1.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )
    assert "channel_binding" not in settings.database_url
    assert "sslmode" not in settings.database_url
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.database_requires_ssl is True


def test_channel_binding_only_url_is_still_stripped():
    settings = Settings(database_url="postgres://user:pass@host.neon.tech/db?channel_binding=require")
    assert "channel_binding" not in settings.database_url


def test_plain_url_with_no_query_params_is_unaffected():
    settings = Settings(database_url="postgresql://mira:mira@localhost:5432/mira_dev")
    assert settings.database_url == "postgresql+asyncpg://mira:mira@localhost:5432/mira_dev"
    assert settings.database_requires_ssl is False


def test_sslmode_disable_does_not_set_requires_ssl():
    settings = Settings(database_url="postgresql://mira:mira@localhost:5432/mira_dev?sslmode=disable")
    assert settings.database_requires_ssl is False
