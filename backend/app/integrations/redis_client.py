"""Optional TTL cache for outbound API responses -- currently only
SearchApi.io calls (app/integrations/searchapi_client.py), which sit on a
small free-tier request allowance shared across every property's live
pricing questions and the daily comparable-pricing refresh job.

Same "don't crash, don't block" pattern as every other optional integration
in this codebase (BRIGHT_DATA_API_KEY, SMTP_*, TWILIO_*): no REDIS_URL, or
any connection failure, and every call here is a silent no-op -- callers
always fall through to fetching live, exactly as if this module didn't
exist. Never raises.

get_client() (below) is also the shared connection accessor for
app/integrations/redis_lease_client.py (CallCoordinator's correctness-
bearing lease operations) -- one Redis connection pool for the whole
process, not two independent clients. That module's own functions are NOT
fail-open the way cache_get_json/cache_set_json here are; see its docstring.
Do not add lease-specific behavior to this file, and do not make
cache_get_json/cache_set_json raise -- the two use cases have deliberately
different failure contracts and must stay in separate modules.
"""

import json
import logging
from typing import Any

from redis.asyncio import Redis

from app.config import settings

logger = logging.getLogger(__name__)

_client: Redis | None = None
_client_initialized = False

# Principal-review finding (CRITICAL): Redis.from_url with no explicit
# socket timeouts defaults BOTH to None -- redis-py will then wait
# INDEFINITELY on a connection attempt or an in-flight command against a
# Redis that is reachable at the TCP level but not responding (a stalled/
# overloaded process, a network partition that black-holes packets instead
# of resetting the connection -- as opposed to "connection refused," which
# fails fast and was the only failure mode originally verified). For
# redis_lease_client.py's correctness-bearing lease operations, an unbounded
# hang here is worse than a fast failure: acquire_or_reject's own
# try/except can never even run (the await itself never returns), which
# would freeze call setup indefinitely -- silently violating the "a Redis
# outage must never block/crash a live guest call" requirement in the one
# way that's actually worse than crashing. 3s covers real Redis round-trip
# latency many times over (single-digit ms on a healthy connection) while
# still failing fast enough that a hung Redis surfaces as the same
# fail-open path a refused connection already takes, not a stall.
_SOCKET_TIMEOUT_SECONDS = 3


def get_client() -> Redis | None:
    """The one place a redis.asyncio.Redis instance is constructed for this
    process. Returns None if REDIS_URL is unset -- callers that need lease
    operations to be correctness-sensitive (not fail-open) must check for
    None themselves and treat it as "Redis unavailable," not silently
    proceed; see redis_lease_client.py."""
    global _client, _client_initialized
    if not _client_initialized:
        _client_initialized = True
        if settings.redis_url:
            _client = Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=_SOCKET_TIMEOUT_SECONDS,
                socket_timeout=_SOCKET_TIMEOUT_SECONDS,
            )
    return _client


async def cache_get_json(key: str) -> Any | None:
    client = get_client()
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
    client = get_client()
    if client is None:
        return
    try:
        await client.set(key, json.dumps(value), ex=ttl_seconds)
    except Exception:
        logger.warning("Redis SET failed for key=%s -- proceeding without caching this response", key)
