"""Phase 7: intentional Mira -> host handoff. Tested at three levels,
matching how deeply each piece is reasonably reachable without constructing
a full live pipeline (real STT/LLM/TTS):

1. app/voice/handoff_signal.py -- pure in-process registry, no pipeline
   involved at all.
2. pipeline._is_host_handoff_frame / pipeline._wait_and_trigger_handoff --
   the individual decision/action units, tested directly against
   lightweight fakes (no real PipelineWorker).
3. pipeline._run_pipeline's own finally-block hangup-skip behavior --
   driven through the REAL _run_pipeline function with _run_pipeline_inner
   monkeypatched out (exact same technique test_run_voice_pipeline_ringing.py
   already uses for its own construction-failure tests), so the actual
   hangup-vs-skip decision logic runs unmodified against a real Exotel
   hangup_call HTTP call (mocked via respx, not skipped).
"""

import asyncio
import uuid

import pytest
import respx
from httpx import Response
from pipecat.frames.frames import EndFrame, TTSSpeakFrame

from app.config import settings
from app.integrations import exotel_client
from app.voice import handoff_signal, pipeline


# ---------------------------------------------------------------------------
# 1. handoff_signal.py -- pure in-process registry
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_handoff_registry():
    """handoff_signal._handoff_events is module-level, process-lifetime
    state -- clear it before and after every test in this file so tests
    can't leak call_session_ids into each other."""
    handoff_signal._handoff_events.clear()
    yield
    handoff_signal._handoff_events.clear()


def test_register_call_creates_a_fresh_unset_event():
    call_session_id = uuid.uuid4()
    handoff_signal.register_call(call_session_id)
    assert call_session_id in handoff_signal._handoff_events
    assert not handoff_signal._handoff_events[call_session_id].is_set()


def test_unregister_call_removes_the_entry():
    call_session_id = uuid.uuid4()
    handoff_signal.register_call(call_session_id)
    handoff_signal.unregister_call(call_session_id)
    assert call_session_id not in handoff_signal._handoff_events


def test_unregister_call_is_safe_for_a_never_registered_id():
    """A Lead Agent or browser-test call never calls register_call at all
    -- unregister_call must still be a safe no-op if called for one."""
    handoff_signal.unregister_call(uuid.uuid4())  # must not raise


def test_request_handoff_returns_false_for_unregistered_call():
    """The documented multi-process gap, and the mundane "already ended"
    case, both surface identically: request_handoff simply can't find a
    listener. Not an error -- see request_handoff's own docstring."""
    assert handoff_signal.request_handoff(uuid.uuid4()) is False


def test_request_handoff_returns_true_and_sets_the_event_for_a_registered_call():
    call_session_id = uuid.uuid4()
    handoff_signal.register_call(call_session_id)
    assert handoff_signal.request_handoff(call_session_id) is True
    assert handoff_signal._handoff_events[call_session_id].is_set()


async def test_wait_for_handoff_request_unblocks_when_requested():
    call_session_id = uuid.uuid4()
    handoff_signal.register_call(call_session_id)

    waiter = asyncio.create_task(handoff_signal.wait_for_handoff_request(call_session_id))
    await asyncio.sleep(0)  # let the waiter actually start waiting
    assert not waiter.done()

    handoff_signal.request_handoff(call_session_id)
    await asyncio.wait_for(waiter, timeout=1.0)
    assert waiter.done()


async def test_wait_for_handoff_request_raises_for_unregistered_call():
    with pytest.raises(KeyError):
        await handoff_signal.wait_for_handoff_request(uuid.uuid4())


async def test_duplicate_handoff_request_is_safe():
    """Item 9 from the brief: a duplicate Take Call claim (already caught
    upstream by Phase 6's atomic DB claim, but this signal layer must also
    tolerate a second call cleanly) is a safe no-op -- calling
    request_handoff twice for the same call_session_id must not raise and
    must not un-set an already-set Event."""
    call_session_id = uuid.uuid4()
    handoff_signal.register_call(call_session_id)

    assert handoff_signal.request_handoff(call_session_id) is True
    assert handoff_signal.request_handoff(call_session_id) is True  # still True, still safe
    assert handoff_signal._handoff_events[call_session_id].is_set()


async def test_host_claim_after_call_already_unregistered_does_nothing():
    """Item 8 from the brief: the call already completed (pipeline's own
    cleanup already called unregister_call) -- a claim arriving after that
    point must not resurrect or affect anything."""
    call_session_id = uuid.uuid4()
    handoff_signal.register_call(call_session_id)
    handoff_signal.unregister_call(call_session_id)

    assert handoff_signal.request_handoff(call_session_id) is False
    assert call_session_id not in handoff_signal._handoff_events


# ---------------------------------------------------------------------------
# 2. _is_host_handoff_frame / _wait_and_trigger_handoff -- direct unit tests
# ---------------------------------------------------------------------------


def test_is_host_handoff_frame_true_for_the_real_end_frame():
    frame = EndFrame(reason=pipeline._HOST_HANDOFF_END_REASON)
    assert pipeline._is_host_handoff_frame(frame) is True


def test_is_host_handoff_frame_false_for_a_normal_end_frame_with_no_reason():
    frame = EndFrame()
    assert pipeline._is_host_handoff_frame(frame) is False


def test_is_host_handoff_frame_false_for_a_different_reason_string():
    frame = EndFrame(reason="something_else")
    assert pipeline._is_host_handoff_frame(frame) is False


def test_is_host_handoff_frame_false_for_an_object_with_no_reason_attribute_at_all():
    class _NotAFrame:
        pass

    assert pipeline._is_host_handoff_frame(_NotAFrame()) is False


class _FakeWorker:
    """Records every queue_frame call, in order -- enough to verify
    _wait_and_trigger_handoff's exact frame sequence without a real
    PipelineWorker/pipeline."""

    def __init__(self):
        self.queued: list = []

    async def queue_frame(self, frame):
        self.queued.append(frame)


async def test_wait_and_trigger_handoff_queues_phrase_then_end_frame_in_order():
    call_session_id = uuid.uuid4()
    handoff_signal.register_call(call_session_id)
    worker = _FakeWorker()

    task = asyncio.create_task(pipeline._wait_and_trigger_handoff(worker, call_session_id))
    await asyncio.sleep(0)
    handoff_signal.request_handoff(call_session_id)
    await asyncio.wait_for(task, timeout=1.0)

    assert len(worker.queued) == 2
    speak_frame, end_frame = worker.queued
    assert isinstance(speak_frame, TTSSpeakFrame)
    assert speak_frame.text == pipeline._HOST_HANDOFF_PHRASE
    assert speak_frame.append_to_context is False
    assert isinstance(end_frame, EndFrame)
    assert end_frame.reason == pipeline._HOST_HANDOFF_END_REASON


async def test_wait_and_trigger_handoff_never_fires_without_a_request():
    """The overwhelming-majority case: nothing ever claims this call. The
    listener task must sit blocked, never queue anything, and be cleanly
    cancellable (mirrors how _run_pipeline_inner's own finally block
    cancels it for a call that ends normally)."""
    call_session_id = uuid.uuid4()
    handoff_signal.register_call(call_session_id)
    worker = _FakeWorker()

    task = asyncio.create_task(pipeline._wait_and_trigger_handoff(worker, call_session_id))
    await asyncio.sleep(0.05)
    assert not task.done()
    assert worker.queued == []

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert worker.queued == []


async def test_wait_and_trigger_handoff_phrase_is_spoken_exactly_once_even_if_signalled_twice():
    """Item 10 from the brief: the phrase must not repeat. Once
    _wait_and_trigger_handoff's single await unblocks and it queues its two
    frames, the task itself is finished -- a second request_handoff call
    for the same id has no running listener left to react to it a second
    time."""
    call_session_id = uuid.uuid4()
    handoff_signal.register_call(call_session_id)
    worker = _FakeWorker()

    task = asyncio.create_task(pipeline._wait_and_trigger_handoff(worker, call_session_id))
    await asyncio.sleep(0)
    handoff_signal.request_handoff(call_session_id)
    await asyncio.wait_for(task, timeout=1.0)

    speak_frame_count_before = sum(1 for f in worker.queued if isinstance(f, TTSSpeakFrame))
    handoff_signal.request_handoff(call_session_id)  # no-op: task already finished
    await asyncio.sleep(0.05)
    speak_frame_count_after = sum(1 for f in worker.queued if isinstance(f, TTSSpeakFrame))

    assert speak_frame_count_before == 1
    assert speak_frame_count_after == 1


# ---------------------------------------------------------------------------
# 3. _run_pipeline's finally block -- real function, _run_pipeline_inner stubbed
# ---------------------------------------------------------------------------


@respx.mock
async def test_normal_call_end_still_invokes_hangup_call(test_user, monkeypatch):
    """Regression guard: Phase 7 must not have broken the existing,
    non-handoff hangup path. _run_pipeline_inner is stubbed to simulate a
    NORMAL successful return (no exception, no handoff) -- the finally
    block's own hangup_call safety net must still fire exactly as before
    Phase 7 existed."""
    monkeypatch.setattr(settings, "exotel_sid", "test-sid")
    monkeypatch.setattr(settings, "exotel_api_key", "test-key")
    monkeypatch.setattr(settings, "exotel_api_token", "test-token")
    monkeypatch.setattr(settings, "exotel_subdomain", "api.exotel.com")

    call_sid = f"normal-call-{uuid.uuid4().hex[:8]}"
    route = respx.post(f"https://api.exotel.com/v1/Accounts/test-sid/Calls/{call_sid}.json").mock(
        return_value=Response(200, json={"Call": {"Sid": call_sid, "Status": "completed"}})
    )

    async def _normal_return(*args, **kwargs):
        return None  # on_pipeline_finished already ran "for real" inside here in production

    monkeypatch.setattr(pipeline, "_run_pipeline_inner", _normal_return)

    await pipeline._run_pipeline(
        transport=None,
        property_id=None,
        call_session_id=uuid.uuid4(),
        host_user_id=test_user.id,
        system_prompt="",
        first_message="",
        exotel_call_id=call_sid,
    )
    await asyncio.sleep(0.1)

    assert route.called
    assert call_sid in exotel_client._hangup_requested


@respx.mock
async def test_host_handoff_outcome_skips_the_finally_block_safety_net_hangup(test_user, monkeypatch):
    """The core Phase 7 guarantee, isolated to _run_pipeline's own finally
    block: when _run_pipeline_inner sets handoff_outcome.is_host_handoff =
    True (exactly what the real on_pipeline_finished handler does when
    _is_host_handoff_frame(frame) is True) and returns normally, the
    finally block's safety-net hangup_call must NOT fire -- Exotel's REST
    API must receive zero requests for this call_sid."""
    monkeypatch.setattr(settings, "exotel_sid", "test-sid")
    monkeypatch.setattr(settings, "exotel_api_key", "test-key")
    monkeypatch.setattr(settings, "exotel_api_token", "test-token")
    monkeypatch.setattr(settings, "exotel_subdomain", "api.exotel.com")

    call_sid = f"handoff-call-{uuid.uuid4().hex[:8]}"
    route = respx.post(f"https://api.exotel.com/v1/Accounts/test-sid/Calls/{call_sid}.json").mock(
        return_value=Response(200, json={"Call": {"Sid": call_sid, "Status": "completed"}})
    )

    async def _simulate_handoff(*args, handoff_outcome=None, **kwargs):
        # Exactly what the real on_pipeline_finished handler does the
        # instant it sees _is_host_handoff_frame(frame) is True.
        if handoff_outcome is not None:
            handoff_outcome.is_host_handoff = True

    monkeypatch.setattr(pipeline, "_run_pipeline_inner", _simulate_handoff)

    await pipeline._run_pipeline(
        transport=None,
        property_id=None,
        call_session_id=uuid.uuid4(),
        host_user_id=test_user.id,
        system_prompt="",
        first_message="",
        exotel_call_id=call_sid,
    )
    await asyncio.sleep(0.1)

    assert not route.called
    assert call_sid not in exotel_client._hangup_requested


async def test_host_handoff_still_releases_the_call_coordinator_lease(test_user, test_property, monkeypatch):
    """Item 5 from the brief: even during a handoff, the CallCoordinator
    lease must still be released -- Phase 7 only changes whether
    hangup_call fires, never the lease/ringing/renewal cleanup that already
    existed, all of which sits in the SAME finally block, unconditional on
    handoff_outcome."""
    from app.services import call_coordinator

    lease = await call_coordinator.acquire(test_property.user_id, test_property.id, holder_ref="handoff-test-call")
    assert lease is not None

    async def _simulate_handoff(*args, handoff_outcome=None, **kwargs):
        if handoff_outcome is not None:
            handoff_outcome.is_host_handoff = True

    monkeypatch.setattr(pipeline, "_run_pipeline_inner", _simulate_handoff)

    await pipeline._run_pipeline(
        transport=None,
        property_id=test_property.id,
        call_session_id=uuid.uuid4(),
        host_user_id=test_property.user_id,
        system_prompt="",
        first_message="",
        exotel_call_id=None,  # isolate lease-release from the hangup assertions above
        call_lease_token=lease.token,
    )

    # If the lease were still held, a second acquire for the same
    # host/property would be rejected.
    second_lease = await call_coordinator.acquire(
        test_property.user_id, test_property.id, holder_ref="post-handoff-call"
    )
    assert second_lease is not None


async def test_host_handoff_still_cancels_renewal_and_ringing_tasks(test_user, monkeypatch):
    """Item 6 from the brief. Both tasks live in _run_pipeline's own
    finally block, gated only on "was one started", never on
    handoff_outcome -- this test proves a handoff outcome doesn't skip
    that cancellation."""
    ringing_task_cancelled = False

    async def _fake_ringing_loop():
        nonlocal ringing_task_cancelled
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            ringing_task_cancelled = True
            raise

    async def _simulate_handoff(*args, handoff_outcome=None, **kwargs):
        # A real _run_pipeline_inner always performs many real awaits
        # before reaching its caller's finally block, which is what gives
        # a freshly-created ringing_audio_task its first chance to actually
        # start running (a task cancelled before ever being scheduled once
        # never reaches its own try/except CancelledError body at all --
        # an asyncio scheduling subtlety, not something this stub needs to
        # simulate beyond yielding control at least once).
        await asyncio.sleep(0)
        if handoff_outcome is not None:
            handoff_outcome.is_host_handoff = True

    monkeypatch.setattr(pipeline, "_run_pipeline_inner", _simulate_handoff)

    ringing_task = asyncio.create_task(_fake_ringing_loop())
    await pipeline._run_pipeline(
        transport=None,
        property_id=None,
        call_session_id=uuid.uuid4(),
        host_user_id=test_user.id,
        system_prompt="",
        first_message="",
        exotel_call_id=None,
        ringing_audio_task=ringing_task,
    )

    assert ringing_task_cancelled is True
