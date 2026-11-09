"""_CallLeaseGuard: the lease is freed the moment the guest is gone, once,
from whichever teardown path gets there first -- so an immediate redial
reaches Mira instead of being told "busy". Uses the real Redis (see
conftest), same as test_call_coordinator.py."""

import asyncio
import uuid

import pytest

from app.services import call_coordinator
from app.voice import pipeline


@pytest.fixture
def availability_calls(monkeypatch):
    calls: list[tuple] = []

    async def _fake_process_availability_recovery(host_user_id, property_id):
        calls.append((host_user_id, property_id))

    monkeypatch.setattr(pipeline.recovery_service, "process_availability_recovery", _fake_process_availability_recovery)
    return calls


async def _drain_background_tasks():
    for _ in range(3):
        await asyncio.sleep(0)


async def _start_call(host, prop):
    decision, lease = await call_coordinator.acquire_or_reject(host, prop, holder_ref="call-1")
    assert decision is call_coordinator.Decision.START_PIPELINE and lease is not None
    return lease


async def test_redial_after_release_reaches_the_agent_not_busy(availability_calls):
    host, prop = uuid.uuid4(), uuid.uuid4()
    lease = await _start_call(host, prop)
    guard = pipeline._CallLeaseGuard(host, prop, lease.token)

    # While the first call is live a second caller is turned away.
    decision, _ = await call_coordinator.acquire_or_reject(host, prop, holder_ref="call-2")
    assert decision is call_coordinator.Decision.BUSY_RECOVERY

    # Guest hangs up; the post-call work has NOT run yet -- only the guard fired.
    await guard.release("client_disconnected")

    decision, new_lease = await call_coordinator.acquire_or_reject(host, prop, holder_ref="call-3")
    assert decision is call_coordinator.Decision.START_PIPELINE
    assert new_lease is not None and new_lease.token != lease.token
    await call_coordinator.release(host, prop, new_lease.token)


async def test_release_is_idempotent_and_triggers_availability_once(availability_calls):
    host, prop = uuid.uuid4(), uuid.uuid4()
    lease = await _start_call(host, prop)
    guard = pipeline._CallLeaseGuard(host, prop, lease.token)

    await guard.release("client_disconnected")
    await guard.release("pipeline_finished")
    await guard.release("teardown")
    await _drain_background_tasks()

    assert availability_calls == [(host, prop)]


async def test_concurrent_release_paths_release_only_once(availability_calls):
    host, prop = uuid.uuid4(), uuid.uuid4()
    lease = await _start_call(host, prop)
    guard = pipeline._CallLeaseGuard(host, prop, lease.token)

    await asyncio.gather(guard.release("client_disconnected"), guard.release("pipeline_finished"))
    await _drain_background_tasks()

    assert availability_calls == [(host, prop)]


async def test_late_release_cannot_free_a_newer_calls_lease(availability_calls):
    """The early release must not make the safety-net `finally` release
    dangerous: after the redialed call takes the lease, the old call's later
    teardown release is a no-op (and does not fire availability processing)."""
    host, prop = uuid.uuid4(), uuid.uuid4()
    lease = await _start_call(host, prop)
    guard = pipeline._CallLeaseGuard(host, prop, lease.token)
    await guard.release("client_disconnected")

    decision, new_lease = await call_coordinator.acquire_or_reject(host, prop, holder_ref="call-2")
    assert decision is call_coordinator.Decision.START_PIPELINE

    await guard.release("teardown")  # the old call finishing its slow post-call work
    await _drain_background_tasks()
    assert await call_coordinator.is_busy(host, prop) is True
    assert availability_calls == [(host, prop)]  # only the first, real release
    await call_coordinator.release(host, prop, new_lease.token)


async def test_release_cancels_the_renewal_task(availability_calls):
    host, prop = uuid.uuid4(), uuid.uuid4()
    lease = await _start_call(host, prop)
    renewal = asyncio.create_task(asyncio.sleep(60))
    guard = pipeline._CallLeaseGuard(host, prop, lease.token, renewal)

    await guard.release("client_disconnected")
    await asyncio.sleep(0)
    assert renewal.cancelled() or renewal.done()


async def test_no_token_is_a_noop(availability_calls):
    guard = pipeline._CallLeaseGuard(uuid.uuid4(), None, None)
    await guard.release("teardown")
    await _drain_background_tasks()
    assert availability_calls == []


async def test_run_pipeline_frees_the_line_while_post_call_work_is_still_running(availability_calls, monkeypatch):
    """Wiring check through the real _run_pipeline: when the inner pipeline
    signals the guest is gone, a redial gets the agent EVEN THOUGH the
    (slow) post-call work in the inner function is still in progress; and the
    final teardown does not release/trigger a second time."""
    host, prop = uuid.uuid4(), uuid.uuid4()
    lease = await _start_call(host, prop)
    redial_decisions = []

    async def _slow_inner(*args, release_call_lease=None, **kwargs):
        assert release_call_lease is not None
        await release_call_lease("client_disconnected")  # guest hangs up
        # ...post-call classification/summary would run from here, for seconds.
        decision, new_lease = await call_coordinator.acquire_or_reject(host, prop, holder_ref="redial")
        redial_decisions.append(decision)
        if new_lease is not None:
            await call_coordinator.release(host, prop, new_lease.token)

    monkeypatch.setattr(pipeline, "_run_pipeline_inner", _slow_inner)

    await pipeline._run_pipeline(
        transport=None,
        property_id=prop,
        call_session_id=uuid.uuid4(),
        host_user_id=host,
        system_prompt="",
        first_message="",
        call_lease_token=lease.token,
    )
    await _drain_background_tasks()

    assert redial_decisions == [call_coordinator.Decision.START_PIPELINE]
    assert availability_calls == [(host, prop)]  # exactly one trigger, from the early release
