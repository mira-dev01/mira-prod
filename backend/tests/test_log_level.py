"""LOG_LEVEL: production must not store pipecat's per-turn prompt dump (DEBUG)."""

import subprocess
import sys
from pathlib import Path

import pytest

from app.config import Settings


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, take_call_token_secret="x" * 40, exotel_webhook_token="real-token", **kwargs)


@pytest.mark.parametrize(
    ("environment", "log_level", "expected"),
    [
        ("production", "auto", "INFO"),
        ("development", "auto", "DEBUG"),
        ("test", "auto", "DEBUG"),
        ("production", "DEBUG", "DEBUG"),
        ("production", "debug", "DEBUG"),  # env var case does not matter
        ("production", "AUTO", "INFO"),
        ("production", " auto ", "INFO"),
        ("development", "WARNING", "WARNING"),
    ],
)
def test_effective_log_level(environment, log_level, expected):
    assert _settings(environment=environment, log_level=log_level).effective_log_level == expected


def test_production_sink_drops_debug_lines_but_keeps_info():
    """Run configure_logging() for real in a fresh process (it replaces loguru's global
    handlers, which would break caplog for every other test in this one)."""
    code = (
        "from loguru import logger\n"
        "from app.observability.logging_setup import configure_logging\n"
        "configure_logging()\n"
        "logger.debug('DEBUG_LINE_MARKER')\n"
        "logger.info('INFO_LINE_MARKER')\n"
    )
    env = {
        "ENVIRONMENT": "production",
        "TAKE_CALL_TOKEN_SECRET": "x" * 40,
        "EXOTEL_WEBHOOK_TOKEN": "real-token",
        "LOG_FORMAT": "text",
        "DATABASE_URL": "postgresql+asyncpg://mira:mira@localhost:5432/mira_test",
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
    }
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    logged = out.stdout + out.stderr
    assert "INFO_LINE_MARKER" in logged, logged
    assert "DEBUG_LINE_MARKER" not in logged
