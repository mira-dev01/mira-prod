"""CallCoordinator: single authority for "does this host/property already
have a live call?" Nothing else. It does not send WhatsApp, does not create
Leads/Notifications, does not touch the dashboard, and does not know the
pipeline exists beyond the opaque holder_ref/token a caller hands it back on
renew/release/transfer.

This module's pipeline-facing entry point, acquire_or_reject, is wired into
app/voice/pipeline.py's run_voice_pipeline, immediately after host/property
resolution. The pipeline itself only ever sees a Decision -- START_PIPELINE
or BUSY_RECOVERY -- never lease internals beyond the Lease dataclass; see
acquire_or_reject's docstring. On BUSY_RECOVERY, the pipeline plays a
placeholder clip and hangs up (app/voice/ringing_audio.play_busy_message +
exotel_client.hangup_call), then fires app/services/recovery_service.py
(NOT part of this module -- CallCoordinator itself still knows nothing
about Leads/Notifications/WhatsApp/hosts) as a detached task to turn the
rejected call into something the host can follow up on.

REDIS MIGRATION (this module no longer touches Postgres at all for lease
state): ownership is a single Redis key per (host, property) pair --
call_lease:{host_user_id}:{property_id} -- holding a small JSON value whose
"token" field is the actual ownership/fencing credential. Correctness comes
from:
  - acquire(): ONE atomic `SET key value NX EX ttl` (app/integrations/
    redis_lease_client.acquire) -- Redis itself rejects a concurrent SET
    for an existing key, atomically, regardless of how many
    processes/workers call it concurrently. No GET-before-SET, no
    application-level lock.
  - renew()/release()/transfer(): each a single atomic Lua script (see
    redis_lease_client.py) that verifies the caller's token still matches
    what's currently stored before mutating anything. This is what
    prevents a stale/delayed caller (e.g. a renewal or release arriving
    late, after its own lease already expired and was re-acquired by a
    DIFFERENT caller) from ever touching a lease it no longer owns -- see
    test_call_coordinator.py's stale-token regression tests for the exact
    scenario this guards against.
  - expires_at: Redis TTL is the entire expiry mechanism now. A key past
    its TTL simply does not exist -- no lazy "is this row still active"
    check is needed the way Postgres's partial-unique-index design
    required (no separate stale-lease reclaim step either: SET NX against
    an already-expired/gone key just succeeds).

app/models/call_lease.py (the Postgres table) still exists in the repo but
is NOT imported or written to by this module anymore -- see that file's own
docstring for the staged-migration note. No production code path depends on
it after this change; it is retained only so a later, separate cleanup
phase can drop it without this migration also being a schema change.
"""

import enum
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.integrations import redis_lease_client
from app.integrations.redis_lease_client import RedisLeaseUnavailable

logger = logging.getLogger(__name__)

# Renewed roughly every 20-30s by a caller during a live call; 45s gives a
# wide safety margin against a single missed renewal tick while still
# capping "stuck busy after a crash" exposure to under a minute. Unchanged
# by the Redis migration -- this reasoning was about renewal cadence vs.
# crash exposure, not about which datastore enforces it.
DEFAULT_LEASE_TTL = timedelta(seconds=45)

PIPELINE_HOLDER_TYPE = "pipeline"

# Sentinel standing in for "no property, this is a Lead Agent call" -- kept
# identical to the Postgres-era CallLease.NIL_PROPERTY_ID (same value) so
# the key format stays a fixed, predictable call_lease:<uuid>:<uuid> shape
# rather than needing a second key pattern for the nullable case. Purely a
# key-naming convenience now (Redis keys have no NULL-distinctness issue
# the way a Postgres unique index did) -- carried forward for continuity,
# not because Redis requires it.
NIL_PROPERTY_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@dataclass(frozen=True)
class Lease:
    """Opaque-to-the-caller handle returned by acquire(). A pipeline (or any
    future holder) threads `token` back into renew()/release()/transfer()
    and never needs to know anything else about how ownership is stored.
    `token` is the actual fencing credential (see module docstring) --
    `holder_ref` is carried along for logging/observability only, it is
    never itself compared for ownership.
    """

    token: str
    holder_ref: str
    expires_at: datetime


class Decision(enum.Enum):
    """The only two outcomes acquire_or_reject() can hand back. Deliberately
    just these two values, nothing richer (no reason code, no lease details
    inline) -- the pipeline's whole contract with CallCoordinator is "which
    of these two things do I do," never "why," so there is nothing else for
    a pipeline caller to branch on or be tempted to inspect.
    """

    START_PIPELINE = "start_pipeline"
    BUSY_RECOVERY = "busy_recovery"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _owner_property_id(property_id: uuid.UUID | None) -> uuid.UUID:
    """Callers of this module pass property_id=None for Lead Agent calls
    (host-scoped, no single property) -- same convention as
    CallSession.property_id. Translated to NIL_PROPERTY_ID here, at the
    module boundary, so every caller (and every other function below) can
    keep using plain None and never needs to know the sentinel exists.
    """
    return property_id if property_id is not None else NIL_PROPERTY_ID


def _lease_key(host_user_id: uuid.UUID, property_id: uuid.UUID | None) -> str:
    return f"call_lease:{host_user_id}:{_owner_property_id(property_id)}"


def _encode_lease(token: str, holder_ref: str, holder_type: str, host_user_id: uuid.UUID, property_id: uuid.UUID) -> str:
    return json.dumps(
        {
            "token": token,
            "holder_ref": holder_ref,
            "holder_type": holder_type,
            "host_user_id": str(host_user_id),
            "property_id": str(property_id),
        }
    )


async def is_busy(host_user_id: uuid.UUID, property_id: uuid.UUID | None) -> bool:
    """Read-only check: does an unexpired lease already exist for this
    (host, property)? Safe to call as often as needed (e.g. by a future
    dashboard/occupancy consumer) -- never writes.

    Fails open (treated as "not busy") on a Redis outage, logged loudly --
    matches acquire_or_reject's own fail-open policy for the same failure
    mode. A caller that needs to distinguish "confirmed free" from "Redis
    was unreachable" should catch RedisLeaseUnavailable itself; this
    function's contract for its one current/anticipated caller (a future
    dashboard occupancy view, per the original design) is "best-effort
    busy indicator," same spirit as every other optional-dependency read in
    this codebase.
    """
    key = _lease_key(host_user_id, property_id)
    try:
        value = await redis_lease_client.get(key)
    except RedisLeaseUnavailable:
        logger.error(
            "lease_redis_unavailable operation=is_busy host_user_id=%s property_id=%s",
            host_user_id,
            _owner_property_id(property_id),
        )
        return False
    return value is not None


async def acquire(
    host_user_id: uuid.UUID,
    property_id: uuid.UUID | None,
    holder_ref: str,
    holder_type: str = PIPELINE_HOLDER_TYPE,
    ttl: timedelta = DEFAULT_LEASE_TTL,
) -> Lease | None:
    """Attempts to claim ownership of this host/property's call line.
    Returns the new Lease on success, or None if another live (unexpired)
    lease already holds it -- callers map that directly to BUSY_RECOVERY
    vs. START_PIPELINE, with no other branch to reason about.

    ONE atomic Redis command (SET key value NX EX ttl_seconds) -- no
    GET/EXISTS beforehand, no separate stale-lease reclaim step (an
    already-expired key simply doesn't exist for SET NX to collide with;
    Redis TTL expiry replaces the Postgres-era lazy-reclaim logic entirely).

    Raises RedisLeaseUnavailable on a genuine connection/configuration
    failure -- this function does NOT itself decide fail-open/fail-closed;
    acquire_or_reject (the pipeline-facing entry point) does, so that
    policy lives in exactly one place.
    """
    owner_property_id = _owner_property_id(property_id)
    key = _lease_key(host_user_id, property_id)
    token = str(uuid.uuid4())
    value = _encode_lease(token, holder_ref, holder_type, host_user_id, owner_property_id)

    acquired = await redis_lease_client.acquire(key, value, int(ttl.total_seconds()))
    if not acquired:
        logger.info(
            "lease_rejected_busy host_user_id=%s property_id=%s holder_type=%s",
            host_user_id,
            owner_property_id,
            holder_type,
        )
        return None

    expires_at = _now() + ttl
    logger.info(
        "lease_acquired host_user_id=%s property_id=%s holder_type=%s holder_ref=%s",
        host_user_id,
        owner_property_id,
        holder_type,
        holder_ref,
    )
    return Lease(token=token, holder_ref=holder_ref, expires_at=expires_at)


async def acquire_or_reject(
    host_user_id: uuid.UUID,
    property_id: uuid.UUID | None,
    holder_ref: str,
    holder_type: str = PIPELINE_HOLDER_TYPE,
    ttl: timedelta = DEFAULT_LEASE_TTL,
) -> tuple[Decision, Lease | None]:
    """The pipeline-facing entry point -- the ONLY function app/voice/pipeline.py
    should ever call. Wraps acquire() and collapses its Lease | None result
    into exactly the two-value contract the pipeline is allowed to see:

        (Decision.START_PIPELINE, lease)  -- ownership was claimed; build
            the real pipeline. `lease` is an opaque handle the pipeline
            threads back into renew()/release()/transfer() later -- it must
            not inspect anything about it beyond passing it along.
        (Decision.BUSY_RECOVERY, None)    -- another live call already
            owns this host/property; do not build a pipeline at all.

    Failure policy (Redis unavailable, or any other unexpected error) lives
    HERE, not in acquire(): fails open to START_PIPELINE, exactly like the
    pre-Redis Postgres version's fail-open-on-IntegrityError-adjacent-errors
    behavior. A live guest call must never be rejected solely because Redis
    is temporarily unreachable -- but unlike a silent fallback, this is
    logged loudly (lease_redis_unavailable) as an explicit, alertable
    operational signal that call-busy protection is degraded, not swallowed
    as if nothing happened. See app/voice/pipeline.py's own comment at the
    call site for why "degraded but available" is the right posture for a
    single-agent architecture (a missed double-booking is bounded/
    recoverable via Busy Call Recovery's own WhatsApp fallback; a rejected
    live call is not recoverable by anything downstream).
    """
    try:
        lease = await acquire(host_user_id, property_id, holder_ref, holder_type=holder_type, ttl=ttl)
    except RedisLeaseUnavailable:
        logger.error(
            "lease_redis_unavailable operation=acquire host_user_id=%s property_id=%s holder_type=%s "
            "holder_ref=%s -- call protection DEGRADED, proceeding as START_PIPELINE",
            host_user_id,
            _owner_property_id(property_id),
            holder_type,
            holder_ref,
        )
        return (Decision.START_PIPELINE, None)
    if lease is None:
        return (Decision.BUSY_RECOVERY, None)
    return (Decision.START_PIPELINE, lease)


async def renew(host_user_id: uuid.UUID, property_id: uuid.UUID | None, token: str, ttl: timedelta = DEFAULT_LEASE_TTL) -> datetime | None:
    """Extends an active lease's expiry -- ONLY if `token` still matches the
    lease currently stored for this (host, property). Returns the new
    expires_at, or None if no active lease with this token exists anymore
    (already released, never existed, expired, or -- critically --
    RE-ACQUIRED BY A DIFFERENT CALLER since this token was issued). A caller
    that gets None has lost ownership and should treat the call as no
    longer protected, not retry blindly.

    Atomic (single Lua script, see redis_lease_client.renew) -- the token
    comparison and the TTL reset happen inside one Redis-side EVAL, so no
    other acquire/renew/release/transfer can interleave between the check
    and the mutation.

    Fails open on a Redis outage: returns None (same externally-visible
    contract as "lease no longer active"), logged, never raises into the
    caller -- a renewal failure must never terminate a live guest
    conversation. The lease will naturally expire via Redis TTL if Redis
    remains reachable; if Redis itself is down, there is nothing left to
    protect anyway.
    """
    owner_property_id = _owner_property_id(property_id)
    key = _lease_key(host_user_id, property_id)
    new_expiry = _now() + ttl
    try:
        renewed = await redis_lease_client.renew(key, token, int(ttl.total_seconds()))
    except RedisLeaseUnavailable:
        logger.warning(
            "lease_redis_unavailable operation=renew host_user_id=%s property_id=%s -- renewal skipped, "
            "existing lease will expire naturally if Redis is genuinely down",
            host_user_id,
            owner_property_id,
        )
        return None
    if not renewed:
        logger.info(
            "lease_renewal_failed host_user_id=%s property_id=%s -- no active lease for this token",
            host_user_id,
            owner_property_id,
        )
        return None
    logger.info("lease_renewed host_user_id=%s property_id=%s", host_user_id, owner_property_id)
    return new_expiry


async def release(host_user_id: uuid.UUID, property_id: uuid.UUID | None, token: str) -> bool:
    """Marks the lease held by `token` as released, freeing the (host,
    property) pair for a new acquire() immediately -- ONLY if `token` still
    matches what's currently stored. Idempotent -- calling this with a
    token that no longer matches (already released, expired, or RE-ACQUIRED
    BY A DIFFERENT CALLER since) is a silent no-op, matching this
    codebase's existing discipline for teardown-path cleanup, since a
    pipeline's finally-block release must be safe to call even when acquire
    never actually succeeded for this call.

    This is the exact mechanism that prevents the failure scenario a stale
    release() call could otherwise cause: Call A acquires (token T1) ->
    Call A's lease expires via Redis TTL -> Call B acquires the same key
    (token T2) -> Call A's release() finally executes with T1 -- the
    atomic Lua script (redis_lease_client.release) compares T1 against
    the CURRENTLY stored token (T2), finds no match, and does NOT delete
    the key. Call B's lease is untouched. See
    test_call_coordinator.py::test_stale_token_cannot_release_a_newer_lease.

    Fails open on a Redis outage: logs and returns False, never raises -- a
    release failure must never crash or endanger the call it's cleaning up
    after (this always runs from a pipeline's finally block, after the
    call has already ended).

    Returns True only when THIS call's token actually held and deleted the
    lease -- False for a Redis outage, a stale/already-released token, or a
    token that never held anything. This is a plain, additive expansion of
    what redis_lease_client.release() already computes and previously
    discarded (see that function's own bool return) -- release()'s actual
    job (verify token, delete if it matches) is unchanged; it now just
    truthfully reports what happened instead of always returning None. The
    one intended caller of this return value is app/voice/pipeline.py's
    _run_pipeline, to decide whether THIS call is the one that actually
    freed the (host, property) pair and should therefore trigger busy-
    recovery availability processing (see recovery_service.py) -- this
    module still has zero knowledge that caller or that concept exists;
    it remains a plain boolean about lease ownership, nothing else.
    """
    owner_property_id = _owner_property_id(property_id)
    key = _lease_key(host_user_id, property_id)
    try:
        released = await redis_lease_client.release(key, token)
    except RedisLeaseUnavailable:
        logger.warning(
            "lease_redis_unavailable operation=release host_user_id=%s property_id=%s",
            host_user_id,
            owner_property_id,
        )
        return False
    if released:
        logger.info("lease_released host_user_id=%s property_id=%s", host_user_id, owner_property_id)
    else:
        logger.info(
            "lease_release_skipped_stale_token host_user_id=%s property_id=%s",
            host_user_id,
            owner_property_id,
        )
    return released


async def transfer(
    host_user_id: uuid.UUID,
    property_id: uuid.UUID | None,
    old_token: str,
    new_holder_ref: str,
    new_holder_type: str,
    ttl: timedelta = DEFAULT_LEASE_TTL,
) -> str | None:
    """Re-points an ACTIVE lease from one holder to another in place --
    gives `holder_type`'s own promise ("human handoff, a second AI agent"
    as future holder types) a real, race-free operation. Verifies
    `old_token` still matches, then atomically replaces holder_ref/
    holder_type AND issues a FRESH token for the new holder, resetting TTL
    -- single Lua script (redis_lease_client.transfer), so there is no
    window where the key is absent (a release-then-acquire handoff would
    have one, letting a third caller steal the (host, property) pair
    mid-handoff) or where the OLD holder's token remains valid against the
    new lease (a handoff must not let the outgoing holder retain any
    ability to renew/release on the new holder's behalf).

    Returns the new token on success (the new holder needs this to
    subsequently renew()/release() with the right credential), or None if
    old_token no longer matches an active lease (already released/expired,
    or belongs to a different, newer lease already).
    """
    owner_property_id = _owner_property_id(property_id)
    key = _lease_key(host_user_id, property_id)
    new_token = str(uuid.uuid4())
    new_value = _encode_lease(new_token, new_holder_ref, new_holder_type, host_user_id, owner_property_id)
    try:
        transferred = await redis_lease_client.transfer(key, old_token, new_value, int(ttl.total_seconds()))
    except RedisLeaseUnavailable:
        logger.warning(
            "lease_transfer_failed host_user_id=%s property_id=%s reason=redis_unavailable",
            host_user_id,
            owner_property_id,
        )
        return None
    if not transferred:
        logger.info(
            "lease_transfer_failed host_user_id=%s property_id=%s reason=stale_or_missing_token",
            host_user_id,
            owner_property_id,
        )
        return None
    logger.info(
        "lease_transfer_succeeded host_user_id=%s property_id=%s new_holder_type=%s new_holder_ref=%s",
        host_user_id,
        owner_property_id,
        new_holder_type,
        new_holder_ref,
    )
    return new_token
