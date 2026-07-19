"""Optional TTL cache for outbound API responses -- currently only
SearchApi.io calls (app/integrations/searchapi_client.py), which sit on a
small free-tier request allowance shared across every property's live
pricing questions and the daily comparable-pricing refresh job.

Same "don't crash, don't block" pattern as every other optional integration
in this codebase (BRIGHT_DATA_API_KEY, SMTP_*, TWILIO_*): no REDIS_URL, or
any connection failure, and every call here is a silent no-op -- callers
always fall through to fetching live, exactly as if this module didn't
exist. Never raises.
"""

import json
import logging
from typing import Any

from redis.asyncio import Redis

from app.config import settings

logger = logging.getLogger(__name__)

_client: Redis | None = None
_client_initialized = False


def _get_client() -> Redis | None:
    global _client, _client_initialized
    if not _client_initialized:
        _client_initialized = True
        if settings.redis_url:
            _client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def cache_get_json(key: str) -> Any | None:
    client = _get_client()
    if client is None:
        return None
    try:
        raw = await client.get(key)
    except Exception:
        logger.warning("Redis GET failed for key=%s -- falling through to a live fetch", key)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


async def cache_set_json(key: str, value: Any, ttl_seconds: int) -> None:
    client = _get_client()
    if client is None:
        return
    try:
        await client.set(key, json.dumps(value), ex=ttl_seconds)
    except Exception:
        logger.warning("Redis SET failed for key=%s -- proceeding without caching this response", key)
