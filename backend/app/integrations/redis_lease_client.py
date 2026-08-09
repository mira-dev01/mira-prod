"""Low-level Redis primitives backing CallCoordinator's active-call
ownership mechanism (app/services/call_coordinator.py). This is the SECOND,
deliberately separate Redis use case in this codebase -- do not confuse it
with app/integrations/redis_client.py's cache_get_json/cache_set_json,
and do not merge the two:

- redis_client.py:      a CACHE. Fail-open by design -- Redis absent or any
                         connection error is a silent no-op, callers always
                         fall through to a live fetch. Correctness never
                         depends on it.
- redis_lease_client.py (here): CORRECTNESS-BEARING call ownership. A Redis
                         failure here must be visible to CallCoordinator,
                         not swallowed -- see RedisLeaseUnavailable below.
                         CallCoordinator (not this module) decides what to
                         do about that failure (acquire fails open to
                         START_PIPELINE with a loud log; renew/release fail
                         open silently, per call_coordinator.py's own
                         docstring) -- this module's job is only to make the
                         failure observable, never to paper over it itself.

Reuses redis_client.get_client() for the actual redis.asyncio.Redis
instance -- one connection pool for the whole process, not a second
independent client. redis-py's default connection pool (created lazily by
Redis.from_url, sized on demand) is intentionally left unconfigured here,
same restraint this codebase already applies to its Postgres pool
(app/database.py has no explicit pool_size either) -- Mira runs a single
uvicorn worker process today (see Dockerfile) and lease traffic is small
(one acquire + one release per call, one renew per 20s per LIVE call), so
there is no basis for picking an arbitrary pool number over redis-py's
default lazy-grows-on-demand behavior. Revisit only if real multi-worker
deployment or measured connection pressure ever calls for it.

Every function here does exactly ONE Redis round trip (a single SET, or one
EVALSHA/EVAL for the Lua scripts) -- no GET-then-SET, no application-level
lock, no polling. See call_coordinator.py for the atomicity requirements
each function exists to satisfy.
"""

import logging

from redis.asyncio import Redis
from redis.commands.core import AsyncScript

from app.integrations import redis_client

logger = logging.getLogger(__name__)


class RedisLeaseUnavailable(Exception):
    """Raised by every function in this module when Redis cannot be reached
    (unconfigured REDIS_URL, connection error, timeout) or returns something
    unexpected. Deliberately NOT swallowed here -- unlike redis_client.py's
    cache functions, a lease operation that silently no-ops on a Redis
    failure would be indistinguishable from "the lease genuinely doesn't
    exist," which is exactly wrong for acquire() (would let two calls both
    believe they succeeded... no, actually the opposite failure: acquire()
    unable to tell if the key exists cannot safely decide anything).
    CallCoordinator catches this and decides the fail-open/fail-loud policy
    per operation -- this module's only job is to make the failure visible.
    """


# Renewal: verifies the caller's token still matches the token embedded in
# the currently-stored lease JSON, then resets TTL on the SAME value (no
# rewrite of holder_ref/holder_type/token -- renewal only ever extends
# expiry, it must never touch lease identity) -- atomically.
# GET-then-compare-then-EXPIRE would have a race window between the GET and
# the EXPIRE where another acquire/transfer could change the key -- this
# script does both inside one server-side EVAL, so no other command can
# interleave. Decodes the stored JSON (cjson, built into Redis's scripting
# engine) and compares only the "token" field -- not a whole-string
# comparison -- so the caller only ever needs to know its own token, not
# reconstruct a byte-identical copy of the full stored value.
_RENEW_SCRIPT = """
local current = redis.call("GET", KEYS[1])
if current == false then
    return 0
end
local decoded = cjson.decode(current)
if decoded["token"] ~= ARGV[1] then
    return 0
end
redis.call("EXPIRE", KEYS[1], ARGV[2])
return 1
"""

# Release: same ownership check as renew, DEL instead of SET on success.
# This is the exact mechanism preventing the scenario call_coordinator.py's
# transfer()/release() docstrings describe: a stale holder's release() call
# arriving after its lease has already expired and been re-acquired by a
# different caller must never delete the NEW caller's key -- the current
# token is compared against the expected token first, inside the same
# atomic script, so a mismatched (stale) caller's release is a pure no-op.
_RELEASE_SCRIPT = """
local current = redis.call("GET", KEYS[1])
if current == false then
    return 0
end
local decoded = cjson.decode(current)
if decoded["token"] ~= ARGV[1] then
    return 0
end
redis.call("DEL", KEYS[1])
return 1
"""

# Transfer: same ownership check, but SET a new value (new token/holder)
# and reset TTL instead of deleting. Re-points the same key in place -- no
# release-then-acquire race window where a third caller could grab the
# (host, property) pair mid-handoff (see call_coordinator.transfer()'s own
# docstring for why that window matters).
_TRANSFER_SCRIPT = """
local current = redis.call("GET", KEYS[1])
if current == false then
    return 0
end
local decoded = cjson.decode(current)
if decoded["token"] ~= ARGV[1] then
    return 0
end
redis.call("SET", KEYS[1], ARGV[2], "EX", ARGV[3])
return 1
"""

_renew_script: AsyncScript | None = None
_release_script: AsyncScript | None = None
_transfer_script: AsyncScript | None = None


def _require_client() -> Redis:
    """Unlike redis_client.get_client()'s callers (which treat None as
    "no-op, fall through"), a lease operation with no configured Redis has
    nothing safe to fall through TO -- there is no other source of truth
    left once Postgres is out of the runtime path (see call_coordinator.py's
    module docstring). Raising here is what lets CallCoordinator's own
    acquire() apply its documented fail-open-but-loud policy instead of this
    module silently pretending a lease was acquired or released."""
    client = redis_client.get_client()
    if client is None:
        raise RedisLeaseUnavailable("REDIS_URL is not configured")
    return client


def _get_scripts(client: Redis) -> tuple[AsyncScript, AsyncScript, AsyncScript]:
    # Registered once per process (module-level cache, not per-call) and
    # reused -- register_script's AsyncScript wrapper transparently retries
    # via SCRIPT LOAD on a NOSCRIPT error (e.g. after a Redis restart or
    # SCRIPT FLUSH), so no manual re-registration handling is needed here.
    global _renew_script, _release_script, _transfer_script
    if _renew_script is None:
        _renew_script = client.register_script(_RENEW_SCRIPT)
        _release_script = client.register_script(_RELEASE_SCRIPT)
        _transfer_script = client.register_script(_TRANSFER_SCRIPT)
    return _renew_script, _release_script, _transfer_script


async def acquire(key: str, value: str, ttl_seconds: int) -> bool:
    """SET key value NX EX ttl_seconds -- ONE atomic command, no GET/EXISTS
    beforehand. Returns True if the key was set (lease acquired), False if
    it already existed (another live lease holds it -- NOT an error, this
    is the expected "busy" outcome). Raises RedisLeaseUnavailable for a
    genuine connection/configuration failure, which is a different outcome
    CallCoordinator must distinguish from "busy" (see acquire()'s own
    fail-open-on-error vs. correctly-reject-on-busy branching)."""
    client = _require_client()
    try:
        result = await client.set(key, value, nx=True, ex=ttl_seconds)
    except RedisLeaseUnavailable:
        raise
    except Exception as exc:
        raise RedisLeaseUnavailable(f"SET NX failed for key={key}") from exc
    return bool(result)


async def get(key: str) -> str | None:
    """Read-only, no ownership implications -- backs is_busy(). A missing
    key (None) is a normal, expected outcome (not busy), not an error."""
    client = _require_client()
    try:
        return await client.get(key)
    except RedisLeaseUnavailable:
        raise
    except Exception as exc:
        raise RedisLeaseUnavailable(f"GET failed for key={key}") from exc


async def renew(key: str, expected_token: str, ttl_seconds: int) -> bool:
    """Atomically verifies expected_token still matches the token embedded
    in the JSON currently stored at key, then resets TTL on the existing
    value (identity fields untouched) -- see _RENEW_SCRIPT. Returns False
    (not an error) if the key is gone or the token no longer matches (lease
    expired and possibly reclaimed by a different caller) -- same "you no
    longer hold this" semantics call_coordinator.renew() already
    documents."""
    client = _require_client()
    renew_script, _, _ = _get_scripts(client)
    try:
        result = await renew_script(keys=[key], args=[expected_token, ttl_seconds])
    except RedisLeaseUnavailable:
        raise
    except Exception as exc:
        raise RedisLeaseUnavailable(f"renew script failed for key={key}") from exc
    return bool(result)


async def release(key: str, expected_token: str) -> bool:
    """Atomically verifies expected_token still matches the token embedded
    in the stored JSON, DELs only if so -- see _RELEASE_SCRIPT. This is what
    makes a stale caller's release() provably unable to delete a different,
    newer lease's key (the exact scenario call_coordinator.py's
    transfer()/release() docstrings call out): the comparison and the
    delete happen inside one Lua script, no other command can run between
    them."""
    client = _require_client()
    _, release_script, _ = _get_scripts(client)
    try:
        result = await release_script(keys=[key], args=[expected_token])
    except RedisLeaseUnavailable:
        raise
    except Exception as exc:
        raise RedisLeaseUnavailable(f"release script failed for key={key}") from exc
    return bool(result)


async def transfer(key: str, expected_token: str, new_lease_json: str, ttl_seconds: int) -> bool:
    """Atomically verifies expected_token still matches, then SETs the new
    value (new token/holder, JSON-encoded) and resets TTL -- see
    _TRANSFER_SCRIPT. Same ownership check as renew(), but re-points the
    lease to a new holder instead of just extending it."""
    client = _require_client()
    _, _, transfer_script = _get_scripts(client)
    try:
        result = await transfer_script(keys=[key], args=[expected_token, new_lease_json, ttl_seconds])
    except RedisLeaseUnavailable:
        raise
    except Exception as exc:
        raise RedisLeaseUnavailable(f"transfer script failed for key={key}") from exc
    return bool(result)
