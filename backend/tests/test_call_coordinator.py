import asyncio
import uuid
from datetime import timedelta

from app.integrations import redis_lease_client
from app.services import call_coordinator

# Redis rejects a negative/zero TTL outright (`SET key value EX -1`/`EX 0`
# both error with "invalid expire time"), unlike the old Postgres design
# where an already-past expires_at could simply be inserted directly.
# call_coordinator truncates ttl to whole seconds for Redis's EX (int(ttl.
# total_seconds())), so anything under 1s truncates to 0 and would hit that
# same error -- 1 whole second is the shortest TTL that's both valid and
# reliably expired by the time the test checks it.
_SHORT_TTL = timedelta(seconds=1)
_SHORT_TTL_WAIT_SECONDS = 1.3


async def test_acquire_succeeds_when_no_lease_exists(test_user, test_property):
    lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1")
    assert lease is not None
    assert lease.holder_ref == "call-1"
    assert lease.token  # a fresh UUID4 was issued


async def test_acquire_rejects_second_call_for_same_host_property(test_user, test_property):
    first = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1")
    assert first is not None

    second = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-2")
    assert second is None


async def test_acquire_allows_different_properties_for_same_host(test_user, test_property, db_session):
    from app.models.property import Property

    other_property = Property(
        user_id=test_user.id,
        name="Second Villa",
        city="Goa",
        exophone=f"+9180{uuid.uuid4().int % 10**8:08d}",
        base_price=3000,
        max_guests=4,
    )
    db_session.add(other_property)
    await db_session.commit()
    await db_session.refresh(other_property)

    first = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1")
    second = await call_coordinator.acquire(test_user.id, other_property.id, holder_ref="call-2")

    assert first is not None
    assert second is not None


async def test_acquire_allows_different_hosts_for_lead_agent_calls(db_session):
    # property_id=None models a Lead Agent call, scoped by host only (see
    # CallSession.property_id's own nullability for the same reason).
    from app.models.user import User

    host_a = User(email=f"a-{uuid.uuid4().hex[:8]}@example.com", clerk_user_id=f"u_{uuid.uuid4().hex[:16]}")
    host_b = User(email=f"b-{uuid.uuid4().hex[:8]}@example.com", clerk_user_id=f"u_{uuid.uuid4().hex[:16]}")
    db_session.add_all([host_a, host_b])
    await db_session.commit()
    await db_session.refresh(host_a)
    await db_session.refresh(host_b)

    lease_a = await call_coordinator.acquire(host_a.id, None, holder_ref="call-a")
    lease_b = await call_coordinator.acquire(host_b.id, None, holder_ref="call-b")

    assert lease_a is not None
    assert lease_b is not None


async def test_acquire_rejects_second_lead_agent_call_for_same_host(test_user):
    first = await call_coordinator.acquire(test_user.id, None, holder_ref="call-1")
    second = await call_coordinator.acquire(test_user.id, None, holder_ref="call-2")

    assert first is not None
    assert second is None


async def test_acquire_reclaims_an_expired_lease(test_user, test_property):
    # Redis TTL replaces Postgres's lazy-reclaim logic entirely -- a genuinely
    # expired key simply does not exist anymore, so SET NX against it just
    # succeeds. No explicit "reclaim" step exists (or is needed) in the new
    # implementation; this proves the same externally-observable behavior.
    expired = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1", ttl=_SHORT_TTL)
    assert expired is not None
    await asyncio.sleep(_SHORT_TTL_WAIT_SECONDS)

    new_lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-2")
    assert new_lease is not None
    assert new_lease.holder_ref == "call-2"


async def test_acquire_or_reject_returns_start_pipeline_with_lease_when_free(test_user, test_property):
    decision, lease = await call_coordinator.acquire_or_reject(test_user.id, test_property.id, holder_ref="call-1")
    assert decision is call_coordinator.Decision.START_PIPELINE
    assert lease is not None
    assert lease.holder_ref == "call-1"


async def test_acquire_or_reject_returns_busy_recovery_with_no_lease_when_taken(test_user, test_property):
    await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1")

    decision, lease = await call_coordinator.acquire_or_reject(test_user.id, test_property.id, holder_ref="call-2")

    assert decision is call_coordinator.Decision.BUSY_RECOVERY
    assert lease is None


async def test_is_busy_false_when_no_lease(test_user, test_property):
    assert await call_coordinator.is_busy(test_user.id, test_property.id) is False


async def test_is_busy_true_after_acquire(test_user, test_property):
    await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1")
    assert await call_coordinator.is_busy(test_user.id, test_property.id) is True


async def test_is_busy_false_for_expired_lease(test_user, test_property):
    await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1", ttl=_SHORT_TTL)
    await asyncio.sleep(_SHORT_TTL_WAIT_SECONDS)
    assert await call_coordinator.is_busy(test_user.id, test_property.id) is False


async def test_release_frees_the_lease_immediately(test_user, test_property):
    lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1")

    await call_coordinator.release(test_user.id, test_property.id, lease.token)

    assert await call_coordinator.is_busy(test_user.id, test_property.id) is False


async def test_release_allows_a_new_acquire_for_the_same_owner(test_user, test_property):
    lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1")
    await call_coordinator.release(test_user.id, test_property.id, lease.token)

    new_lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-2")
    assert new_lease is not None


async def test_release_is_idempotent_and_never_raises(test_user, test_property):
    await call_coordinator.release(test_user.id, test_property.id, "never-acquired-token")
    await call_coordinator.release(test_user.id, test_property.id, "never-acquired-token")


async def test_renew_extends_expiry_for_active_lease(test_user, test_property):
    lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1")
    assert lease is not None

    new_expiry = await call_coordinator.renew(test_user.id, test_property.id, lease.token, ttl=timedelta(seconds=90))

    assert new_expiry is not None
    assert new_expiry > lease.expires_at


async def test_renew_returns_none_for_unknown_holder(test_user, test_property):
    result = await call_coordinator.renew(test_user.id, test_property.id, "no-such-token")
    assert result is None


async def test_renew_returns_none_after_release(test_user, test_property):
    lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1")
    await call_coordinator.release(test_user.id, test_property.id, lease.token)

    result = await call_coordinator.renew(test_user.id, test_property.id, lease.token)
    assert result is None


async def test_renew_does_not_resurrect_an_already_expired_lease(test_user, test_property):
    # Regression test: renew() must not blindly extend any row matching the
    # token -- only one that is genuinely still active. A holder renewing
    # slightly late (past its own TTL) must not be able to keep a lease
    # alive that another acquire() is already entitled to reclaim as stale
    # -- otherwise a crashed-but-still-renewing holder (or just a slow
    # renewal tick) could block a real caller forever, defeating the whole
    # point of TTL-based expiry. Under Redis, an expired key is simply gone
    # (GET returns nil), so the Lua script's very first check already
    # returns "no active lease" -- same outcome as the old Postgres
    # active_clause check, enforced by TTL instead of an explicit WHERE.
    lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1", ttl=_SHORT_TTL)
    await asyncio.sleep(_SHORT_TTL_WAIT_SECONDS)

    result = await call_coordinator.renew(test_user.id, test_property.id, lease.token, ttl=timedelta(seconds=90))

    assert result is None
    # And the slot must still be genuinely reclaimable afterward.
    new_lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-2")
    assert new_lease is not None


async def test_renew_racing_a_stale_reclaim_never_leaves_two_active_leases(test_user, test_property):
    # A holder renewing right as its lease crosses its TTL, concurrently
    # with a different caller reclaiming the same slot as stale, must never
    # both succeed -- that would leave two "active" holders for the same
    # (host, property), silently defeating the uniqueness the whole design
    # depends on.
    lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1", ttl=_SHORT_TTL)
    await asyncio.sleep(_SHORT_TTL_WAIT_SECONDS)

    async def _renew():
        return await call_coordinator.renew(test_user.id, test_property.id, lease.token, ttl=timedelta(seconds=90))

    async def _reclaim():
        return await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-2")

    renew_result, reclaim_result = await asyncio.gather(_renew(), _reclaim())

    # At most one of the two may have actually kept/claimed the slot.
    assert not (renew_result is not None and reclaim_result is not None)
    assert await call_coordinator.is_busy(test_user.id, test_property.id) is True


async def test_transfer_repoints_an_active_lease_to_a_new_holder(test_user, test_property):
    lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="pipeline-call-1")
    assert lease is not None

    new_token = await call_coordinator.transfer(
        test_user.id, test_property.id, lease.token, new_holder_ref="human-handoff-1", new_holder_type="human"
    )
    assert new_token is not None
    assert new_token != lease.token

    # Still busy (the lease moved, it wasn't released) -- and specifically
    # renewable/releasable only under the NEW token, not the old one.
    assert await call_coordinator.is_busy(test_user.id, test_property.id) is True
    assert await call_coordinator.renew(test_user.id, test_property.id, lease.token) is None
    assert await call_coordinator.renew(test_user.id, test_property.id, new_token) is not None


async def test_transfer_returns_none_for_unknown_or_released_holder(test_user, test_property):
    result = await call_coordinator.transfer(
        test_user.id, test_property.id, "no-such-token", new_holder_ref="new-holder", new_holder_type="human"
    )
    assert result is None

    lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1")
    await call_coordinator.release(test_user.id, test_property.id, lease.token)
    result2 = await call_coordinator.transfer(
        test_user.id, test_property.id, lease.token, new_holder_ref="new-holder", new_holder_type="human"
    )
    assert result2 is None


async def test_transfer_never_leaves_a_gap_where_a_third_caller_can_acquire(test_user, test_property):
    # The whole point of transfer() over release()-then-acquire(): no
    # interleaving window where the (host, property) pair reads as free.
    lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="pipeline-call-1")

    await call_coordinator.transfer(
        test_user.id, test_property.id, lease.token, new_holder_ref="human-handoff-1", new_holder_type="human"
    )

    third = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-3")
    assert third is None


async def test_transfer_extends_expiry_from_now(test_user, test_property):
    # A handoff must not inherit a stale, about-to-expire deadline from the
    # holder it's taking over from.
    lease = await call_coordinator.acquire(
        test_user.id, test_property.id, holder_ref="pipeline-call-1", ttl=timedelta(seconds=1)
    )
    assert lease is not None

    new_token = await call_coordinator.transfer(
        test_user.id,
        test_property.id,
        lease.token,
        new_holder_ref="human-handoff-1",
        new_holder_type="human",
        ttl=timedelta(seconds=90),
    )
    assert new_token is not None

    new_expiry = await call_coordinator.renew(test_user.id, test_property.id, new_token, ttl=timedelta(seconds=90))
    assert new_expiry is not None
    assert new_expiry > lease.expires_at


async def test_concurrent_acquire_only_one_winner(test_user, test_property):
    # Independent asyncio tasks hitting the real Redis connection
    # concurrently -- exercises the actual SET NX atomicity the whole
    # design depends on, not just sequential calls against one connection.
    async def _acquire(holder_ref: str):
        return await call_coordinator.acquire(test_user.id, test_property.id, holder_ref=holder_ref)

    results = await asyncio.gather(_acquire("call-1"), _acquire("call-2"))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1


async def test_concurrent_acquire_many_callers_only_one_winner(test_user, test_property):
    # Same property as above but with higher concurrency (20 simultaneous
    # acquire attempts) -- a stronger proof that SET NX's atomicity holds
    # under real contention, not just a two-way race.
    async def _acquire(i: int):
        return await call_coordinator.acquire(test_user.id, test_property.id, holder_ref=f"call-{i}")

    results = await asyncio.gather(*(_acquire(i) for i in range(20)))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1


# --- Redis-specific regression tests ---------------------------------------


async def test_stale_token_cannot_release_a_newer_lease(test_user, test_property):
    # THE core scenario this migration exists to prevent:
    #   Call A owns token T1
    #   T1 expires
    #   Call B acquires T2
    #   Call A finally executes release()
    #   -> Call B's lease must remain intact.
    stale_lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-a", ttl=_SHORT_TTL)
    assert stale_lease is not None
    await asyncio.sleep(_SHORT_TTL_WAIT_SECONDS)  # now genuinely expired

    new_lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-b")
    assert new_lease is not None
    assert new_lease.token != stale_lease.token

    # Call A's delayed release() finally arrives, using its OLD token.
    await call_coordinator.release(test_user.id, test_property.id, stale_lease.token)

    # Call B's lease must be completely untouched.
    assert await call_coordinator.is_busy(test_user.id, test_property.id) is True
    assert await call_coordinator.renew(test_user.id, test_property.id, new_lease.token) is not None


async def test_stale_token_cannot_renew_a_newer_lease(test_user, test_property):
    stale_lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-a", ttl=_SHORT_TTL)
    await asyncio.sleep(_SHORT_TTL_WAIT_SECONDS)
    new_lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-b")
    assert new_lease is not None

    # Call A's delayed renew() finally arrives, using its OLD token -- must
    # not extend/resurrect anything, and must specifically not touch B's TTL.
    result = await call_coordinator.renew(test_user.id, test_property.id, stale_lease.token, ttl=timedelta(seconds=90))
    assert result is None
    assert await call_coordinator.is_busy(test_user.id, test_property.id) is True


async def test_stale_token_cannot_transfer_a_newer_lease(test_user, test_property):
    stale_lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-a", ttl=_SHORT_TTL)
    await asyncio.sleep(_SHORT_TTL_WAIT_SECONDS)
    new_lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-b")
    assert new_lease is not None

    result = await call_coordinator.transfer(
        test_user.id, test_property.id, stale_lease.token, new_holder_ref="thief", new_holder_type="human"
    )
    assert result is None
    # B's lease must still be renewable under its ORIGINAL token -- proving
    # the transfer attempt didn't silently mutate it.
    assert await call_coordinator.renew(test_user.id, test_property.id, new_lease.token) is not None


async def test_redis_outage_during_acquire_fails_open_and_is_logged(test_user, test_property, monkeypatch, caplog):
    async def _boom(*args, **kwargs):
        raise redis_lease_client.RedisLeaseUnavailable("simulated Redis outage")

    monkeypatch.setattr(redis_lease_client, "acquire", _boom)

    import logging

    with caplog.at_level(logging.ERROR, logger="app.services.call_coordinator"):
        decision, lease = await call_coordinator.acquire_or_reject(test_user.id, test_property.id, holder_ref="call-1")

    assert decision is call_coordinator.Decision.START_PIPELINE
    assert lease is None
    assert any("lease_redis_unavailable" in record.message for record in caplog.records)


async def test_redis_outage_during_renewal_does_not_raise(test_user, test_property, monkeypatch):
    lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1")

    async def _boom(*args, **kwargs):
        raise redis_lease_client.RedisLeaseUnavailable("simulated Redis outage")

    monkeypatch.setattr(redis_lease_client, "renew", _boom)

    result = await call_coordinator.renew(test_user.id, test_property.id, lease.token)
    assert result is None  # fails open, no exception propagates


async def test_redis_outage_during_release_does_not_raise(test_user, test_property, monkeypatch):
    lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1")

    async def _boom(*args, **kwargs):
        raise redis_lease_client.RedisLeaseUnavailable("simulated Redis outage")

    monkeypatch.setattr(redis_lease_client, "release", _boom)

    # Must not raise.
    await call_coordinator.release(test_user.id, test_property.id, lease.token)


async def test_lua_scripts_survive_a_script_flush_noscript(test_user, test_property):
    # register_script's AsyncScript wrapper transparently re-registers via
    # SCRIPT LOAD on a NOSCRIPT error (e.g. after `SCRIPT FLUSH`, which a
    # Redis restart or ops action can trigger) -- this proves that recovery
    # path actually works end-to-end against the real client, not just that
    # redis-py claims to support it.
    from app.integrations import redis_client as rc

    lease = await call_coordinator.acquire(test_user.id, test_property.id, holder_ref="call-1")
    client = rc.get_client()
    await client.script_flush()

    # renew() must still work -- AsyncScript re-registers the script on the
    # NOSCRIPT error it gets back, transparently.
    result = await call_coordinator.renew(test_user.id, test_property.id, lease.token, ttl=timedelta(seconds=90))
    assert result is not None


async def test_hung_redis_connection_fails_open_within_the_socket_timeout(test_user, test_property):
    # Principal-review regression (CRITICAL, found live): Redis.from_url was
    # constructed with no socket_connect_timeout/socket_timeout, both of
    # which default to None in redis-py -- a Redis that is reachable at the
    # TCP level but never responds (a stalled/overloaded process, or a
    # network partition that drops packets instead of resetting the
    # connection -- NOT the same as "connection refused," which already
    # failed fast before this fix) would hang acquire_or_reject's own await
    # INDEFINITELY. That's worse than crashing: the try/except around
    # acquire_or_reject in app/voice/pipeline.py can never even run, so
    # "Redis outage fails open" was false for this specific, realistic
    # failure mode. Verified live before the fix: an identical raw socket
    # server (accepts, never responds) hung a bare redis-py SET call past
    # 6+ seconds with no configured timeout. This test proves the FIX --
    # app.integrations.redis_client.get_client() now sets both timeouts --
    # by pointing at a real TCP listener that accepts but never replies, and
    # asserting acquire_or_reject still returns within a bounded window.
    import socket
    import threading
    import time

    import app.integrations.redis_client as redis_client_module
    from app.config import settings

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))  # OS-assigned free port
    hung_port = server.getsockname()[1]
    server.listen(5)
    stop = threading.Event()

    def _accept_and_hang():
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
                # Accept the connection but never read or write -- this is
                # the exact "reachable but unresponsive" failure mode a
                # connection-refused test can't exercise.
            except socket.timeout:
                continue

    thread = threading.Thread(target=_accept_and_hang, daemon=True)
    thread.start()

    original_url = settings.redis_url
    original_client = redis_client_module._client
    original_initialized = redis_client_module._client_initialized
    try:
        settings.redis_url = f"redis://127.0.0.1:{hung_port}/0"
        redis_client_module._client = None
        redis_client_module._client_initialized = False

        t0 = time.monotonic()
        decision, lease = await call_coordinator.acquire_or_reject(test_user.id, test_property.id, holder_ref="call-1")
        elapsed = time.monotonic() - t0

        assert decision is call_coordinator.Decision.START_PIPELINE
        assert lease is None
        # Must resolve within the configured socket timeout plus generous
        # slack for scheduling jitter -- NOT hang indefinitely (the pre-fix
        # behavior, confirmed separately to still be hanging past 6s).
        assert elapsed < 8, f"acquire_or_reject took {elapsed:.1f}s against a hung Redis -- timeout not applied"
    finally:
        stop.set()
        server.close()
        thread.join(timeout=2)
        settings.redis_url = original_url
        redis_client_module._client = original_client
        redis_client_module._client_initialized = original_initialized
