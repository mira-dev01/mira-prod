import asyncio
import json
import uuid
from datetime import timedelta

import respx
from httpx import Response
from pipecat.runner.types import CallData
from sqlalchemy import select

from app.config import settings
from app.integrations import exotel_client
from app.models.call_session import CallSession
from app.services import call_coordinator, lead_service, recovery_service
from app.services.notification_service import list_notifications
from app.voice import pipeline


class _FakeWebSocket:
    """Records send_text calls and never actually closes -- close() just
    flips a flag, mirroring the real websocket.close() call site closely
    enough for this test without needing a live connection."""

    def __init__(self):
        self.sent: list[str] = []
        self.closed = False

    async def send_text(self, data: str):
        self.sent.append(data)

    async def close(self):
        self.closed = True


async def test_no_matching_property_or_lead_line_cancels_the_ringing_task(db_session):
    # run_voice_pipeline's early-return branch (no Property/lead exophone
    # configured for the dialed number) must still stop the ringing task it
    # started at the top -- otherwise a misdialed/unconfigured number would
    # leave a ring tone looping forever against a websocket nothing else is
    # managing. No fixtures seed a property/lead for this made-up number, so
    # the real DB (per this repo's no-DB-mocking policy) genuinely has no
    # match and the early-return branch is what actually runs.
    ws = _FakeWebSocket()
    call_data = CallData(
        stream_id="stream-unmatched",
        call_id="call-unmatched",
        **{"from": "+919999999999", "to": "+910000000000"},
    )

    await pipeline.run_voice_pipeline(ws, call_data)

    assert ws.closed is True
    # The task was created inside run_voice_pipeline itself and is not
    # returned -- so the only externally observable proof it was actually
    # stopped (not left orphaned) is that the function returned at all
    # without hanging, plus that no frames kept arriving after return.
    sent_at_return = len(ws.sent)
    await asyncio.sleep(0.1)
    assert len(ws.sent) == sent_at_return


async def test_busy_recovery_plays_placeholder_and_hangs_up_without_building_a_pipeline(
    test_property, db_session
):
    # A second concurrent call for a property CallCoordinator already has an
    # active lease for must never reach pipeline construction at all -- see
    # the Decision.BUSY_RECOVERY branch in run_voice_pipeline, immediately
    # after host/property resolution. Real Redis (no mocking, same
    # no-mock-the-real-dependency policy this repo already applies to
    # Postgres): the pre-existing lease is acquired directly against Redis
    # before run_voice_pipeline's own acquire_or_reject call runs.
    lease = await call_coordinator.acquire(test_property.user_id, test_property.id, holder_ref="already-live-call")
    assert lease is not None

    ws = _FakeWebSocket()
    call_data = CallData(
        stream_id="stream-busy",
        call_id="second-call",
        **{"from": "+919999999999", "to": test_property.exophone},
    )

    await pipeline.run_voice_pipeline(ws, call_data)

    assert ws.closed is True
    assert ws.sent  # the placeholder clip was actually played
    first_frame = json.loads(ws.sent[0])
    assert first_frame["event"] == "media"
    assert first_frame["streamSid"] == "stream-busy"

    # No CallSession row for the rejected call -- BUSY_RECOVERY returns
    # before get_or_create_call_session is ever reached.
    rejected_session = await db_session.scalar(
        select(CallSession).where(CallSession.exotel_call_id == "second-call")
    )
    assert rejected_session is None

    # The original lease is untouched -- rejection must not release or
    # otherwise disturb the call that's actually still live.
    assert await call_coordinator.is_busy(test_property.user_id, test_property.id) is True

    # RecoveryService is fired detached (asyncio.create_task) from the
    # BUSY_RECOVERY branch, not awaited by run_voice_pipeline itself -- same
    # fire-and-forget contract as every WhatsApp/email send elsewhere in this
    # codebase (see recovery_service.py's module docstring). A brief real
    # sleep lets that task actually run before asserting its effects, same
    # style already used elsewhere in this file for timing-sensitive checks.
    await asyncio.sleep(0.1)
    leads = await lead_service.list_leads(db_session, test_property.user_id)
    assert any(lead.recovery_reason == recovery_service.RECOVERY_REASON_BUSY_CALL for lead in leads)
    notifications = await list_notifications(db_session)
    assert any(n.channel == recovery_service.NOTIFICATION_CHANNEL_BUSY_RECOVERY for n in notifications)


async def test_call_proceeds_normally_when_no_other_lease_is_active(test_property, db_session):
    # Sanity check for the non-busy path through the same new branch: a
    # property with no existing lease must not be short-circuited by
    # CallCoordinator -- confirmed by a lease existing for it WHILE the call
    # is still in progress (acquire_or_reject's START_PIPELINE branch always
    # returns one). Checked BEFORE cancelling, not after: _run_pipeline's
    # finally block now correctly releases the lease on any exit path,
    # including cancellation (see test_run_pipeline_releases_the_lease_even_
    # when_pipeline_construction_fails) -- asserting is_busy after cancelling
    # would be asserting stale, pre-fix behavior. Deliberately does not drive
    # the pipeline all the way into real STT/TTS/session construction (unlike
    # the BUSY_RECOVERY test above, which never reaches that code at all) --
    # doing so requires real Sarvam credentials this test env doesn't have
    # and makes this test slow/flaky for a fact CallCoordinator's own test
    # suite already covers directly (test_call_coordinator.py's
    # acquire_or_reject tests); this test only needs to prove the pipeline
    # branch didn't take the BUSY_RECOVERY exit.
    ws = _FakeWebSocket()
    call_data = CallData(
        stream_id="stream-free",
        call_id="first-call",
        **{"from": "+919999999999", "to": test_property.exophone},
    )

    task = asyncio.create_task(pipeline.run_voice_pipeline(ws, call_data))
    # Only enough time for CallCoordinator's own acquire() to have committed
    # a lease -- run_voice_pipeline blocks for the lifetime of a real call
    # past this point.
    await asyncio.sleep(0.1)

    assert await call_coordinator.is_busy(test_property.user_id, test_property.id) is True

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass  # pipeline construction failing past this point (no real
              # Sarvam/Groq credentials in a test env) is irrelevant here.


async def test_run_pipeline_releases_the_lease_even_when_pipeline_construction_fails(
    test_user, monkeypatch
):
    # Regression test: acquire_or_reject's lease must be released in
    # _run_pipeline's own guaranteed finally block, not only from
    # on_pipeline_finished -- a construction-time failure (Sarvam/LLM
    # service init, etc) raises before on_pipeline_finished is ever
    # registered, so relying on it alone would leave the lease held until
    # its TTL lazily expires, wrongly blocking a real subsequent call to the
    # same host/property. Exercises _run_pipeline directly (same pattern
    # test_ringing_audio.py uses for play_ringing_tone in isolation) rather
    # than the full run_voice_pipeline chain, since the actual failure mode
    # under test is generic ("_run_pipeline_inner raised"), not specific to
    # any one construction step -- monkeypatching _run_pipeline_inner to
    # raise immediately is a faster, deterministic stand-in for a real
    # Sarvam/LLM construction failure without needing real credentials.
    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated pipeline construction failure")

    monkeypatch.setattr(pipeline, "_run_pipeline_inner", _boom)

    lease = await call_coordinator.acquire(test_user.id, None, holder_ref="doomed-call")
    assert lease is not None

    try:
        await pipeline._run_pipeline(
            transport=None,
            property_id=None,
            call_session_id=uuid.uuid4(),  # arbitrary UUID, never read by the stub
            host_user_id=test_user.id,
            system_prompt="",
            first_message="",
            call_lease_token=lease.token,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the monkeypatched failure to propagate")

    assert await call_coordinator.is_busy(test_user.id, None) is False


@respx.mock
async def test_run_pipeline_hangs_up_exotel_even_when_pipeline_construction_fails(
    test_user, monkeypatch
):
    # Regression for the Principal Engineer review's "pipeline construction
    # failure" gap: on_pipeline_finished (the normal hangup trigger) is
    # registered deep inside _run_pipeline_inner, well after STT/LLM/TTS/
    # Pipeline/PipelineWorker construction -- all fallible. A construction-
    # time failure raises before that registration ever happens, so its
    # hangup_call was never reached, leaving a real Exotel call connected
    # with nothing driving it toward hangup. _run_pipeline's own finally
    # block now unconditionally requests the hangup too (via the same
    # exotel_client.hangup_call + _spawn_background_task machinery
    # on_pipeline_finished uses), so this must fire even though
    # on_pipeline_finished itself never ran. Same monkeypatched-
    # _run_pipeline_inner stand-in as the lease-release regression test
    # above, for the same reason (generic "construction raised", not
    # specific to any one real dependency).
    monkeypatch.setattr(settings, "exotel_sid", "test-sid")
    monkeypatch.setattr(settings, "exotel_api_key", "test-key")
    monkeypatch.setattr(settings, "exotel_api_token", "test-token")
    monkeypatch.setattr(settings, "exotel_subdomain", "api.exotel.com")

    route = respx.post(
        "https://api.exotel.com/v1/Accounts/test-sid/Calls/construction-failure-call.json"
    ).mock(return_value=Response(200, json={"Call": {"Sid": "construction-failure-call", "Status": "completed"}}))

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated pipeline construction failure")

    monkeypatch.setattr(pipeline, "_run_pipeline_inner", _boom)

    try:
        await pipeline._run_pipeline(
            transport=None,
            property_id=None,
            call_session_id=uuid.uuid4(),
            host_user_id=test_user.id,
            system_prompt="",
            first_message="",
            exotel_call_id="construction-failure-call",
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the monkeypatched failure to propagate")

    # The hangup is fired as a detached background task (deliberately not
    # awaited by _run_pipeline, same as on_pipeline_finished's own hangup --
    # Exotel's REST latency must not gate teardown) -- give it a moment to
    # actually run before asserting on it.
    await asyncio.sleep(0.1)

    assert route.called
    # Membership, not equality -- _hangup_requested is a module-level set
    # shared across the whole test session (see exotel_client.py), so other
    # tests' own successfully-hung-up call_sids may already be in it.
    assert "construction-failure-call" in exotel_client._hangup_requested


async def test_run_pipeline_original_exception_is_not_swallowed_by_the_hangup_safety_net(
    test_user, monkeypatch
):
    # The finally block's new hangup request must never mask or replace the
    # real construction failure -- the caller (run_voice_pipeline) still
    # needs to see it (e.g. for its own logging/fail-open behavior).
    monkeypatch.setattr(settings, "exotel_sid", None)  # hangup_call itself no-ops (skipped, no credentials)

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated pipeline construction failure")

    monkeypatch.setattr(pipeline, "_run_pipeline_inner", _boom)

    try:
        await pipeline._run_pipeline(
            transport=None,
            property_id=None,
            call_session_id=uuid.uuid4(),
            host_user_id=test_user.id,
            system_prompt="",
            first_message="",
            exotel_call_id="some-call-id",
        )
    except RuntimeError as e:
        assert "simulated pipeline construction failure" in str(e)
    else:
        raise AssertionError("expected the original RuntimeError to propagate, not be swallowed")


@respx.mock
async def test_run_pipeline_finally_block_hangup_is_a_noop_when_on_pipeline_finished_already_requested_it(
    test_user, monkeypatch
):
    # On a normal, successful call, on_pipeline_finished already requests
    # the real hangup -- _run_pipeline's finally block also unconditionally
    # requests it (see that block's own comment for why unconditional is
    # the deliberate, simpler design), which must resolve to exactly ONE
    # real HTTP call, not two, via exotel_client.hangup_call's own per-
    # call_sid idempotency guard -- not any new state this fix adds.
    monkeypatch.setattr(settings, "exotel_sid", "test-sid")
    monkeypatch.setattr(settings, "exotel_api_key", "test-key")
    monkeypatch.setattr(settings, "exotel_api_token", "test-token")
    monkeypatch.setattr(settings, "exotel_subdomain", "api.exotel.com")

    route = respx.post(
        "https://api.exotel.com/v1/Accounts/test-sid/Calls/already-hungup-call.json"
    ).mock(return_value=Response(200, json={"Call": {"Sid": "already-hungup-call", "Status": "completed"}}))

    # Simulate on_pipeline_finished having already run and requested the
    # hangup for real (as it does on every successful call) before
    # _run_pipeline_inner returns and the finally block runs.
    async def _already_hung_up(*args, **kwargs):
        await exotel_client.hangup_call("already-hungup-call")

    monkeypatch.setattr(pipeline, "_run_pipeline_inner", _already_hung_up)

    await pipeline._run_pipeline(
        transport=None,
        property_id=None,
        call_session_id=uuid.uuid4(),
        host_user_id=test_user.id,
        system_prompt="",
        first_message="",
        exotel_call_id="already-hungup-call",
    )

    await asyncio.sleep(0.1)  # let the finally block's background task run

    assert route.call_count == 1


async def test_run_pipeline_renews_the_lease_periodically_during_a_long_call(test_user, monkeypatch):
    # Cleanup-pass regression test: call_coordinator.renew() previously had
    # no production caller at all -- DEFAULT_LEASE_TTL (45s) was therefore a
    # hard cap on call length before the lease silently went stale mid-call,
    # wrongly letting a second concurrent call through as START_PIPELINE
    # instead of BUSY_RECOVERY. _run_pipeline must now keep the lease alive
    # for as long as _run_pipeline_inner is actually running. Redis rejects
    # sub-1-second EX values (see test_call_coordinator.py's _SHORT_TTL
    # comment) and call_coordinator truncates ttl to whole seconds for
    # Redis's EX -- 1s is the shortest usable TTL here.
    monkeypatch.setattr(pipeline, "_LEASE_RENEWAL_INTERVAL_SECONDS", 0.3)

    inner_started = asyncio.Event()
    release_inner = asyncio.Event()

    async def _slow_inner(*args, **kwargs):
        inner_started.set()
        await release_inner.wait()

    monkeypatch.setattr(pipeline, "_run_pipeline_inner", _slow_inner)

    lease = await call_coordinator.acquire(test_user.id, None, holder_ref="long-call", ttl=timedelta(seconds=1))
    assert lease is not None

    task = asyncio.create_task(
        pipeline._run_pipeline(
            transport=None,
            property_id=None,
            call_session_id=uuid.uuid4(),
            host_user_id=test_user.id,
            system_prompt="",
            first_message="",
            call_lease_token=lease.token,
        )
    )
    await inner_started.wait()

    # Wait well past the original 1s TTL -- without renewal, the lease
    # would already have gone stale (is_busy would flip to False) by now.
    await asyncio.sleep(1.5)
    assert await call_coordinator.is_busy(test_user.id, None) is True

    release_inner.set()
    await task

    # Released cleanly once the (now-complete) call actually finishes, same
    # as every other exit path through _run_pipeline's finally block.
    assert await call_coordinator.is_busy(test_user.id, None) is False


async def test_lease_renewal_loop_stops_quietly_once_the_lease_is_gone(test_user, monkeypatch):
    # If the lease is released/expired out from under the renewal loop (e.g.
    # a lost race, or released early by something else), the loop must
    # notice renew() returning None and simply stop -- not raise into the
    # still-live call.
    monkeypatch.setattr(pipeline, "_LEASE_RENEWAL_INTERVAL_SECONDS", 0.05)

    lease = await call_coordinator.acquire(test_user.id, None, holder_ref="vanishing-lease")
    assert lease is not None
    await call_coordinator.release(test_user.id, None, lease.token)

    task = asyncio.create_task(pipeline._renew_call_lease_periodically(test_user.id, None, lease.token))
    await asyncio.sleep(0.2)

    assert task.done()
    assert task.exception() is None


async def test_lease_renewal_loop_uses_the_token_not_a_stale_lease(test_user, monkeypatch):
    # Redis-specific regression: if the renewal loop's own lease has already
    # expired and a DIFFERENT caller has re-acquired the same (host,
    # property) slot, the loop's next renewal tick (using its now-stale
    # token) must not be able to touch that newer lease -- it must notice
    # it no longer holds anything (renew() returns None) and stop, leaving
    # the new lease completely untouched. Drives call_coordinator.renew()
    # directly with the exact same arguments the background loop itself
    # would use on its next tick, rather than racing real wall-clock time
    # against the loop's own renewal cadence (which would just keep the
    # original lease alive indefinitely, proving nothing).
    lease = await call_coordinator.acquire(test_user.id, None, holder_ref="call-a", ttl=timedelta(seconds=1))
    assert lease is not None
    await asyncio.sleep(1.3)  # let it genuinely expire, no renewal loop running

    new_lease = await call_coordinator.acquire(test_user.id, None, holder_ref="call-b")
    assert new_lease is not None

    # The stale loop's next tick, if it were still running, would call
    # exactly this.
    stale_renewal = await call_coordinator.renew(test_user.id, None, lease.token)
    assert stale_renewal is None

    # And the new lease must be completely unaffected by that stale attempt.
    assert await call_coordinator.is_busy(test_user.id, None) is True
    assert await call_coordinator.renew(test_user.id, None, new_lease.token) is not None


async def test_coordinator_failure_fails_open_instead_of_rejecting_the_call(
    test_property, monkeypatch
):
    # Regression test: acquire_or_reject raising anything (a Redis outage,
    # not the "someone else already holds this" busy case acquire() itself
    # handles) must never turn away a real guest with no actual conflicting
    # call -- see run_voice_pipeline's try/except around the
    # acquire_or_reject call. Without that guard, run_voice_pipeline itself
    # raises the RuntimeError straight out of the acquire_or_reject call
    # site -- fast (no real pipeline construction ever starts) and
    # synchronously enough that the task is already done, with the
    # RuntimeError as its result, by the time this test's own sleep(0.1)
    # elapses. With the guard, the RuntimeError is caught and logged inside
    # run_voice_pipeline, and the call proceeds into real pipeline
    # construction (confirmed via the "failing open" log line and real
    # Sarvam STT initialization occurring when running this test directly),
    # which is still running (not done) at the sleep(0.1) mark -- that's the
    # actual distinguishing signal asserted below, not just "no exception
    # visible to the test," which task.cancel()'s own broad exception
    # swallowing elsewhere in this file would otherwise mask.
    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated Redis outage")

    monkeypatch.setattr(call_coordinator, "acquire_or_reject", _boom)

    ws = _FakeWebSocket()
    call_data = CallData(
        stream_id="stream-coordinator-down",
        call_id="call-coordinator-down",
        **{"from": "+919999999999", "to": test_property.exophone},
    )

    task = asyncio.create_task(pipeline.run_voice_pipeline(ws, call_data))
    await asyncio.sleep(0.1)

    assert not task.done(), (
        "run_voice_pipeline already finished (crashed) before reaching real "
        "pipeline construction -- the acquire_or_reject failure was not "
        "caught and failed open as expected"
    )

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass  # pipeline construction failing past this point (no real
              # Sarvam/Groq credentials in a test env) is irrelevant here.

    assert ws.closed is False


async def test_run_pipeline_triggers_availability_recovery_only_when_lease_actually_released(
    test_user, monkeypatch
):
    # Regression: _run_pipeline's finally block must fire
    # recovery_service.process_availability_recovery ONLY when
    # call_coordinator.release() returns True for THIS call's own token --
    # i.e. this call's own release genuinely freed the (host, property)
    # lease. Never speculatively on every call teardown, and never on a
    # no-op release (stale/already-released token, Redis outage) -- see
    # pipeline.py's own comment at that call site. Monkeypatches
    # process_availability_recovery itself (not respx/Twilio) since this
    # test is about WHETHER/WHEN the trigger fires, not what the recovery
    # service does once triggered (see test_availability_recovery.py for
    # that).
    triggered_with: list = []

    async def _fake_process_availability(host_user_id, property_id):
        triggered_with.append((host_user_id, property_id))

    monkeypatch.setattr(recovery_service, "process_availability_recovery", _fake_process_availability)

    async def _noop_inner(*args, **kwargs):
        pass

    monkeypatch.setattr(pipeline, "_run_pipeline_inner", _noop_inner)

    lease = await call_coordinator.acquire(test_user.id, None, holder_ref="real-release-call")
    assert lease is not None

    await pipeline._run_pipeline(
        transport=None,
        property_id=None,
        call_session_id=uuid.uuid4(),
        host_user_id=test_user.id,
        system_prompt="",
        first_message="",
        call_lease_token=lease.token,
    )

    await asyncio.sleep(0.1)  # let the detached background task actually run
    assert triggered_with == [(test_user.id, None)]


async def test_run_pipeline_does_not_trigger_availability_recovery_on_stale_token_release(
    test_user, monkeypatch
):
    # The inverse: a token that no longer holds an active lease (already
    # released, expired, or re-acquired by a different caller) makes
    # release() return False -- must NOT trigger availability processing,
    # since this call did not actually free anything (someone/something
    # else already did, or there was never anything to free).
    triggered = asyncio.Event()

    async def _fake_process_availability(host_user_id, property_id):
        triggered.set()

    monkeypatch.setattr(recovery_service, "process_availability_recovery", _fake_process_availability)

    async def _noop_inner(*args, **kwargs):
        pass

    monkeypatch.setattr(pipeline, "_run_pipeline_inner", _noop_inner)

    # A token that was never actually acquired -- release() must return
    # False for it (see call_coordinator.release's own stale-token test
    # coverage in test_call_coordinator.py).
    await pipeline._run_pipeline(
        transport=None,
        property_id=None,
        call_session_id=uuid.uuid4(),
        host_user_id=test_user.id,
        system_prompt="",
        first_message="",
        call_lease_token="never-acquired-token",
    )

    await asyncio.sleep(0.1)
    assert not triggered.is_set()


async def test_run_pipeline_does_not_trigger_availability_recovery_when_no_lease_token(test_user, monkeypatch):
    # BUSY_RECOVERY / fail-open-without-lease callers pass call_lease_token=
    # None (see run_voice_pipeline) -- _run_pipeline must not call
    # release() at all in that case (nothing to release), and therefore
    # must not trigger availability processing either.
    triggered = asyncio.Event()

    async def _fake_process_availability(host_user_id, property_id):
        triggered.set()

    monkeypatch.setattr(recovery_service, "process_availability_recovery", _fake_process_availability)

    async def _noop_inner(*args, **kwargs):
        pass

    monkeypatch.setattr(pipeline, "_run_pipeline_inner", _noop_inner)

    await pipeline._run_pipeline(
        transport=None,
        property_id=None,
        call_session_id=uuid.uuid4(),
        host_user_id=test_user.id,
        system_prompt="",
        first_message="",
        call_lease_token=None,
    )

    await asyncio.sleep(0.1)
    assert not triggered.is_set()
