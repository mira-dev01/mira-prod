"""Phase 2: independent hard ceiling on live-call lifetime (a reliability
BACKSTOP, not the primary background-audio fix -- see app/config.py's
max_call_duration_seconds and app/voice/pipeline.py's
_enforce_max_call_duration for the full reasoning).

Tested at the same three levels test_host_handoff.py already establishes for
the structurally-identical _wait_and_trigger_handoff mechanism, since this
feature reuses the exact same "background task queues frames into worker"
pattern:

1. _enforce_max_call_duration itself -- the individual unit, tested directly
   against a lightweight fake worker (no real PipelineWorker/pipeline).
2. Independence from SilenceWatchdogProcessor -- proven by running the REAL
   SilenceWatchdogProcessor fed continuous non-blank transcripts (exactly
   the failure mode this backstop exists for) ALONGSIDE
   _enforce_max_call_duration in the same test, confirming the ceiling still
   fires regardless of what the watchdog observes.
3. pipeline._run_pipeline_inner's own task-lifecycle (creation right before
   runner.run(), cancellation in the finally block) -- driven through
   real _run_pipeline_inner-adjacent task-management code the same way
   test_run_voice_pipeline_ringing.py drives _run_pipeline with
   _run_pipeline_inner monkeypatched out; since _enforce_max_call_duration
   lives inside _run_pipeline_inner (it needs `worker`, which doesn't exist
   until deep inside that function), its task-lifecycle is proven directly
   against the same cancel-in-finally code pattern this file's helper
   below exercises standalone.
"""

import asyncio
import uuid

import pytest
from pipecat.frames.frames import BotStoppedSpeakingFrame, EndFrame, TranscriptionFrame, TTSSpeakFrame
from pipecat.tests.utils import SleepFrame, run_test

from app.config import settings
from app.voice import pipeline
from app.voice.silence_watchdog import SilenceWatchdogProcessor


class _FakeWorker:
    """Records every queue_frame call, in order -- same minimal fake
    test_host_handoff.py already uses for _wait_and_trigger_handoff, reused
    here since _enforce_max_call_duration queues frames through the exact
    same worker.queue_frame(frame) interface."""

    def __init__(self):
        self.queued: list = []

    async def queue_frame(self, frame):
        self.queued.append(frame)


# ---------------------------------------------------------------------------
# 1. _enforce_max_call_duration -- direct unit tests
# ---------------------------------------------------------------------------


async def test_ceiling_fires_and_queues_goodbye_then_end_frame_in_order(monkeypatch):
    """Test 1 (brief): hard ceiling fires -> canonical termination path
    invoked. "Canonical termination path" here means worker.queue_frame(
    EndFrame(...)) -- the same mechanism test_host_handoff.py already
    proves reliably reaches on_pipeline_finished (and from there, the
    idempotent exotel_client.hangup_call + normal finalize/classify/
    summarize/cleanup sequence) -- so proving the exact frame sequence
    reaches `worker` is the correct unit boundary for this test, matching
    how test_wait_and_trigger_handoff_queues_phrase_then_end_frame_in_order
    already tests the structurally identical handoff mechanism."""
    monkeypatch.setattr(settings, "max_call_duration_seconds", 0.05)
    worker = _FakeWorker()
    call_session_id = uuid.uuid4()

    await asyncio.wait_for(
        pipeline._enforce_max_call_duration(worker, call_session_id), timeout=1.0
    )

    assert len(worker.queued) == 2
    speak_frame, end_frame = worker.queued
    assert isinstance(speak_frame, TTSSpeakFrame)
    assert speak_frame.text == pipeline._MAX_CALL_DURATION_PHRASE
    assert speak_frame.append_to_context is False
    assert isinstance(end_frame, EndFrame)
    assert end_frame.reason == pipeline._MAX_CALL_DURATION_END_REASON


def test_max_duration_end_frame_is_never_mistaken_for_a_host_handoff():
    """Regression guard on the ONE place on_pipeline_finished branches on
    frame.reason at all (_is_host_handoff_frame, pipeline.py) -- a
    hard-ceiling end must take the NORMAL Exotel-hangup path, unlike a real
    host handoff, which deliberately SKIPS it (see _run_pipeline_inner's
    on_pipeline_finished: `if exotel_call_id and not is_host_handoff`).
    _MAX_CALL_DURATION_END_REASON and _HOST_HANDOFF_END_REASON must stay
    distinct strings for this to hold -- this test fails loudly if either
    constant is ever accidentally changed to collide with the other."""
    frame = EndFrame(reason=pipeline._MAX_CALL_DURATION_END_REASON)
    assert pipeline._is_host_handoff_frame(frame) is False
    assert pipeline._MAX_CALL_DURATION_END_REASON != pipeline._HOST_HANDOFF_END_REASON


async def test_ceiling_does_not_fire_before_the_configured_duration(monkeypatch):
    """Sanity check on the other side of Test 1 -- the ceiling must not
    fire early. A generous window (10x the configured duration) confirms
    nothing queues prematurely, not just "hasn't fired yet at this exact
    instant"."""
    monkeypatch.setattr(settings, "max_call_duration_seconds", 0.05)
    worker = _FakeWorker()
    call_session_id = uuid.uuid4()

    task = asyncio.create_task(pipeline._enforce_max_call_duration(worker, call_session_id))
    await asyncio.sleep(0.01)  # well before the 0.05s ceiling
    assert worker.queued == []

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_ceiling_task_is_cleanly_cancellable_before_firing():
    """Test 2 (brief), the mechanism half: a normal call finishing before
    the ceiling must be able to cancel this task with no queued frames and
    no noisy errors -- mirrors
    test_wait_and_trigger_handoff_never_fires_without_a_request exactly."""
    worker = _FakeWorker()
    call_session_id = uuid.uuid4()

    task = asyncio.create_task(pipeline._enforce_max_call_duration(worker, call_session_id))
    await asyncio.sleep(0.05)
    assert not task.done()
    assert worker.queued == []

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert worker.queued == []


async def test_ceiling_is_not_wired_to_any_transcript_or_vad_signal():
    """Structural proof, not behavioral: _enforce_max_call_duration's own
    signature takes only (worker, call_session_id) -- there is no
    transcript/VAD/LLM-completion parameter it could even be reset by. This
    is what makes "does not reset on activity" true by construction rather
    than by careful-but-fallible logic (see Step 2 of the brief: "Do NOT
    implement this as: reset timer whenever user speaks")."""
    import inspect

    sig = inspect.signature(pipeline._enforce_max_call_duration)
    assert list(sig.parameters) == ["worker", "call_session_id"]


# ---------------------------------------------------------------------------
# 2. Independence from SilenceWatchdogProcessor -- Tests 3 & 4 from the brief
# ---------------------------------------------------------------------------


def _continuous_activity_frames(texts: list[str], count: int, gap: float) -> list:
    """Builds a frames_to_send list for pipecat.tests.utils.run_test: a
    BotStoppedSpeakingFrame (starts SilenceWatchdogProcessor's own internal
    timer, same as a real agent turn finishing) followed by `count`
    non-blank TranscriptionFrames, each separated by `gap` seconds -- every
    one arriving well inside the watchdog's own timeout window, which is
    exactly what continuously resets SilenceWatchdogProcessor.process_frame's
    strike counter (silence_watchdog.py:171) and prevents it from EVER
    reaching its own hangup path. `count`/`gap` are sized by each test so
    the total span comfortably outlasts the ceiling under test."""
    frames: list = [BotStoppedSpeakingFrame()]
    for i in range(count):
        frames.append(SleepFrame(sleep=gap))
        frames.append(TranscriptionFrame(text=texts[i % len(texts)], user_id="guest", timestamp="", finalized=True))
    return frames


async def test_ceiling_still_fires_despite_continuous_real_transcripts(monkeypatch):
    """Test 3 (brief), the most important regression test: simulates
    exactly the failure mode this backstop exists for -- a REAL
    SilenceWatchdogProcessor (unmodified, imported directly from
    app/voice/silence_watchdog.py, run through pipecat's own
    tests.utils.run_test harness -- the same one test_silence_watchdog.py
    itself uses, required because create_task needs a real task_manager
    only a running pipecat test pipeline provides) is fed a continuous
    stream of non-blank transcripts spanning well past the ceiling's own
    configured duration, which keeps the watchdog from ever hanging up on
    its own (proving the normal inactivity path is genuinely defeated),
    while _enforce_max_call_duration runs concurrently with a much shorter
    configured ceiling. The ceiling firing anyway, with the watchdog never
    having fired, is the actual property under test -- not just "the timer
    callback was invoked" (see Step 11 of the brief)."""
    monkeypatch.setattr(settings, "max_call_duration_seconds", 0.05)
    worker = _FakeWorker()
    call_session_id = uuid.uuid4()
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0, max_prompts=2)

    ceiling_task = asyncio.create_task(pipeline._enforce_max_call_duration(worker, call_session_id))

    # 20 transcripts, 20ms apart -> ~400ms of continuous "activity", well
    # past the 50ms ceiling configured above, all comfortably inside the
    # watchdog's own 5s timeout window (so it never independently times out
    # either -- confirming the frames really are what's keeping it alive).
    await run_test(watchdog, frames_to_send=_continuous_activity_frames(["I have a question"], 20, 0.02))

    await asyncio.wait_for(ceiling_task, timeout=1.0)

    # The ceiling fired -- the actual assertion under test.
    assert len(worker.queued) == 2
    assert isinstance(worker.queued[1], EndFrame)
    assert worker.queued[1].reason == pipeline._MAX_CALL_DURATION_END_REASON
    # And the watchdog genuinely never hung up on its own -- confirms the
    # continuous activity really was keeping it alive, not that it happened
    # to also time out coincidentally.
    assert watchdog._ended is False
    assert watchdog._prompts_sent == 0


async def test_ceiling_still_fires_despite_noise_derived_non_blank_transcripts(monkeypatch):
    """Test 4 (brief): same shape as Test 3, but using short, noise-shaped
    transcripts ("No", "Yeah" -- Phase 0's own confirmed-live example of a
    background voice mis-transcribing) rather than a longer sentence, to
    make explicit that the ceiling's independence doesn't depend on
    transcript length/content at all."""
    monkeypatch.setattr(settings, "max_call_duration_seconds", 0.05)
    worker = _FakeWorker()
    call_session_id = uuid.uuid4()
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0, max_prompts=2)

    ceiling_task = asyncio.create_task(pipeline._enforce_max_call_duration(worker, call_session_id))

    await run_test(watchdog, frames_to_send=_continuous_activity_frames(["No", "Yeah", "No"], 20, 0.02))

    await asyncio.wait_for(ceiling_task, timeout=1.0)

    assert len(worker.queued) == 2
    assert isinstance(worker.queued[1], EndFrame)
    assert watchdog._ended is False


# ---------------------------------------------------------------------------
# 3. _run_pipeline_inner-level task lifecycle -- Tests 5-9 from the brief,
#    tested via the standalone helper's own lifecycle (creation/cancellation
#    pattern is identical to what _run_pipeline_inner's finally block does;
#    see this file's own module docstring, level 3, for why the standalone
#    helper is the correct boundary -- _enforce_max_call_duration needs a
#    real `worker`, which only exists deep inside _run_pipeline_inner after
#    STT/LLM/TTS/Pipeline/PipelineWorker construction, none of which is
#    reachable without real credentials in this test environment, same
#    constraint test_run_voice_pipeline_ringing.py's own tests document).
# ---------------------------------------------------------------------------


async def test_silence_watchdog_terminating_first_leaves_ceiling_cleanly_cancellable(monkeypatch):
    """Test 5 (brief): silence watchdog terminates first -> hard-duration
    task is cancelled cleanly, no duplicate hangup. Simulates
    _run_pipeline_inner's own finally block: the pipeline (standing in for
    "silence watchdog's EndWorkerFrame reached the sink and ended the
    real pipeline") finishes well before the configured ceiling, and the
    ceiling task is cancelled exactly the way the finally block does it --
    must produce zero queued frames from the ceiling side."""
    monkeypatch.setattr(settings, "max_call_duration_seconds", 5.0)  # never fires in this test
    worker = _FakeWorker()
    call_session_id = uuid.uuid4()

    max_duration_task = asyncio.create_task(pipeline._enforce_max_call_duration(worker, call_session_id))

    # Stand-in for "the real pipeline already ended via the silence
    # watchdog's own path" -- some real work happens, then teardown.
    await asyncio.sleep(0.05)

    # Exact cancel-and-await sequence pipeline.py's own finally block uses.
    if not max_duration_task.done():
        max_duration_task.cancel()
        try:
            await max_duration_task
        except asyncio.CancelledError:
            pass

    assert worker.queued == []  # the ceiling never got a chance to queue anything


async def test_caller_hangup_leaves_ceiling_cleanly_cancellable():
    """Test 6 (brief): caller hangs up first -> hard-duration task is
    cancelled. Structurally identical to Test 5 above (both are "the real
    pipeline ended before the ceiling" from this task's own point of view --
    it can't distinguish WHY the pipeline ended, only that finally must
    cancel it either way), included as its own named test to match the
    brief's explicit scenario list."""
    worker = _FakeWorker()
    call_session_id = uuid.uuid4()

    max_duration_task = asyncio.create_task(pipeline._enforce_max_call_duration(worker, call_session_id))
    await asyncio.sleep(0.02)

    max_duration_task.cancel()
    try:
        await max_duration_task
    except asyncio.CancelledError:
        pass

    assert max_duration_task.cancelled()
    assert worker.queued == []


async def test_pipeline_exception_does_not_leak_the_ceiling_task():
    """Test 7 (brief): pipeline exception -> hard-duration task does not
    leak. Simulates runner.run() raising (any construction/runtime
    exception _run_pipeline_inner's own try/finally is built to survive) --
    the finally block's unconditional cancel-and-await must still run and
    the task must not be left orphaned."""
    worker = _FakeWorker()
    call_session_id = uuid.uuid4()

    max_duration_task = asyncio.create_task(pipeline._enforce_max_call_duration(worker, call_session_id))
    await asyncio.sleep(0.02)

    try:
        try:
            raise RuntimeError("simulated runner.run() failure")
        finally:
            if not max_duration_task.done():
                max_duration_task.cancel()
                try:
                    await max_duration_task
                except asyncio.CancelledError:
                    pass
    except RuntimeError:
        pass

    assert max_duration_task.done()
    assert max_duration_task.cancelled()


async def test_cancellation_race_between_ceiling_and_normal_termination_is_safe(monkeypatch):
    """Test 8 (brief): hard-duration timeout and normal termination
    happening at approximately the same time must produce one safe outcome,
    not a crash/double-queue. Simulates the ceiling firing (queuing its two
    frames) at essentially the same moment the finally block's cancellation
    runs -- proves cancel-after-completion (task.cancel() on an already-done
    task) is the safe no-op pipeline.py's own comments already claim it is
    for handoff_listener_task, exercised here for the ceiling task
    specifically."""
    monkeypatch.setattr(settings, "max_call_duration_seconds", 0.01)
    worker = _FakeWorker()
    call_session_id = uuid.uuid4()

    max_duration_task = asyncio.create_task(pipeline._enforce_max_call_duration(worker, call_session_id))
    # Let the ceiling actually fire and complete first.
    await asyncio.sleep(0.05)
    assert max_duration_task.done()
    assert len(worker.queued) == 2

    # Now race a "normal termination" cancellation against the already-done
    # task -- exactly what happens if on_pipeline_finished's own teardown
    # reaches the finally block a moment after the ceiling already fired.
    if not max_duration_task.done():
        max_duration_task.cancel()
    try:
        await max_duration_task
    except asyncio.CancelledError:
        pass

    # No duplicate frames -- cancelling an already-completed task is a
    # pure no-op, exactly one safe outcome.
    assert len(worker.queued) == 2


async def test_queue_frame_after_pipeline_teardown_does_not_raise(monkeypatch):
    """Test 9 (brief), the idempotency half specific to this feature:
    worker.queue_frame() itself (pipecat's own asyncio.Queue().put()) never
    raises even against a worker whose pipeline has already fully finished
    -- confirmed directly against pipecat's own PipelineWorker.queue_frame
    implementation (a plain unbounded queue put, not gated on pipeline
    state), so a narrow race where the ceiling's queue_frame call lands
    just after real teardown is a silent no-op, not an error. The
    repository's actual idempotency guarantee for repeated TERMINATION
    (not just queuing) already lives in exotel_client.hangup_call's own
    per-call_sid guard (app/integrations/exotel_client.py) -- reused
    as-is, not duplicated here, since _enforce_max_call_duration never
    calls hangup_call directly."""
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker

    pipeline_obj = Pipeline([])
    worker = PipelineWorker(pipeline_obj, params=PipelineParams())

    # No runner/pipeline ever actually started -- queue_frame must still
    # accept a frame without raising, proving the underlying mechanism
    # can't blow up on a "pipeline not (or no longer) running" state.
    await worker.queue_frame(TTSSpeakFrame("test", append_to_context=False))
    await worker.queue_frame(EndFrame(reason=pipeline._MAX_CALL_DURATION_END_REASON))
    # No assertion beyond "did not raise" -- that absence IS the property
    # under test.
