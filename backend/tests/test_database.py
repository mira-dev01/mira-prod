"""Scale Readiness ("Phase 17"): confirms the engine's connection pool is
explicitly configured from settings, not silently left at whatever
SQLAlchemy's own library defaults happen to be -- see database.py's and
config.py's own comments for why this matters at higher call volume."""

from app.config import settings
from app.database import engine


def test_engine_pool_size_matches_settings():
    assert engine.pool.size() == settings.db_pool_size


def test_engine_pool_overflow_matches_settings():
    assert engine.pool._max_overflow == settings.db_max_overflow


def test_engine_pool_timeout_matches_settings():
    assert engine.pool._timeout == settings.db_pool_timeout


def test_engine_pool_recycle_matches_settings():
    assert engine.pool._recycle == settings.db_pool_recycle_seconds


def test_engine_pool_pre_ping_still_enabled():
    """Regression: pool_recycle is a proactive age ceiling that complements
    pool_pre_ping's reactive per-checkout check -- confirms this phase
    didn't accidentally drop the pre-existing pre_ping setting while adding
    the new pool args."""
    assert engine.pool._pre_ping is True


def test_default_pool_settings_match_previous_implicit_sqlalchemy_defaults():
    """Behavior-preserving by default: unless overridden via env vars, the
    new explicit settings must equal what SQLAlchemy silently defaulted to
    before this phase (pool_size=5, max_overflow=10, pool_timeout=30.0) --
    only pool_recycle is a deliberate, new, proactive addition (previously
    unset/never-recycle)."""
    assert settings.db_pool_size == 5
    assert settings.db_max_overflow == 10
    assert settings.db_pool_timeout == 30.0
