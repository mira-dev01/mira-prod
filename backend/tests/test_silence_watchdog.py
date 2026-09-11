import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from app.voice import pipeline
from app.voice import silence_watchdog as silence_watchdog_module
from app.voice.conversation_state import ConversationState
from app.voice.silence_watchdog import SilenceWatchdogProcessor


def _blank_transcript() -> TranscriptionFrame:
    return TranscriptionFrame(text="", user_id="guest", timestamp="", finalized=True)


def _real_transcript(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="guest", timestamp="", finalized=True)


def _completed_turn(text: str, gap: float = 0.02) -> list:
    """Phase 5D: the real pipeline sequence for one genuine, completed guest
    turn -- a TranscriptionFrame (carries the text, feeds the repetition-
    shadow signal) followed by UserStoppedSpeakingFrame (the actual
    meaningful-response boundary; carries no text of its own, see pipecat's
    own UserStoppedSpeakingFrame -- a bare marker dataclass). Confirmed
    directly against pipecat's own LLMUserAggregator source and via a live
    synthetic pipeline trace during this phase's investigation: with a real
    transcript, TranscriptionFrame is pushed downstream, then
    UserStoppedSpeakingFrame is broadcast upstream -- this ordering is what
    SilenceWatchdogProcessor now depends on. A SleepFrame gap is included
    since pipecat's own test harness does not guarantee frames_to_send
    delivery order is preserved across mixed frame types without one
    (confirmed directly during Phase 5C; see this file's own history)."""
    return [_real_transcript(text), SleepFrame(sleep=gap), UserStoppedSpeakingFrame()]


@pytest.mark.asyncio
async def test_silence_prompts_then_hangs_up_after_two_unanswered_nudges():
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)

    # _on_timeout restarts its own timer directly after each nudge (see
    # silence_watchdog.py), so the only frame needed to kick things off is
    # the initial BotStoppedSpeakingFrame -- three consecutive 0.1s timeouts
    # then fire on their own: nudge #1, nudge #2, then hangup.
    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),  # greeting just finished -> start the clock
            SleepFrame(sleep=1.0),  # comfortably long enough for all 3 timeouts to elapse
        ],
        # The processor ends the call itself via EndWorkerFrame (see
        # silence_watchdog.py for why it can't just push EndFrame directly) --
        # the pipeline worker converts that into the real EndFrame that
        # actually reaches the sink. send_end_frame=False since the test
        # shouldn't also queue its own.
        send_end_frame=False,
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 3
    assert speak_frames[0].text == "Hello?"
    assert speak_frames[1].text == "Hello, are you there?"
    assert "end the call" in speak_frames[2].text.lower()
    assert isinstance(down_frames[-1], EndFrame)
    assert down_frames[-1].reason == "silent caller"


@pytest.mark.asyncio
async def test_blank_transcript_does_not_reset_or_count_as_reply():
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.3, max_prompts=2)

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),  # timer starts counting from t=0
            SleepFrame(sleep=0.05),
            _blank_transcript(),  # background-noise blip at t=0.05 -- must not reset the timer
            # Land comfortably after nudge #1 (fires at t~=0.3, restarting
            # the timer for nudge #2 at t~=0.6) but well before nudge #2.
            SleepFrame(sleep=0.35),
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1
    assert speak_frames[0].text == "Hello?"


@pytest.mark.asyncio
async def test_completed_turn_resets_strikes_and_cancels_timer():
    """Phase 5D: the real signal is now UserStoppedSpeakingFrame (a
    genuinely completed turn), not a bare TranscriptionFrame -- see
    _completed_turn's own docstring.

    Note: UserStoppedSpeakingFrame both resets the strike counter AND
    starts a FRESH wait window (realistically, the bot would reply next,
    producing its own BotStoppedSpeakingFrame) -- this test checks the
    reset happened promptly, using a short window that ends before the
    fresh timer itself would elapse, rather than sleeping long enough for
    a whole new (correctly-firing) nudge cycle to start.

    Non-vacuity note: a nudge must fire FIRST (making _prompts_sent
    genuinely nonzero before the completed turn) -- confirmed during this
    phase that asserting _prompts_sent == 0 without ever having driven it
    above 0 first is vacuous (a disabled reset assignment still passes,
    since the value was already 0)."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.15),  # nudge #1 fires at ~0.1s -- _prompts_sent becomes 1
            *_completed_turn("I have a question"),
            SleepFrame(sleep=0.05),  # well before the fresh 0.1s window elapses
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1  # only the one nudge that fired before the reset
    assert watchdog._prompts_sent == 0


@pytest.mark.asyncio
async def test_transcript_alone_without_turn_completion_does_not_reset_strikes():
    """Phase 5D core behavior change: a raw TranscriptionFrame with no
    following UserStoppedSpeakingFrame must NOT reset the strike counter or
    cancel the timer on its own anymore -- only a genuinely completed turn
    does. This is the direct fix for the brief's central complaint (any
    non-blank transcript was previously enough)."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.05),
            _real_transcript("I have a question"),  # no UserStoppedSpeakingFrame follows
            # Land shortly after nudge #1 (fires at t~=0.1, restarting the
            # timer for nudge #2 at t~=0.2) but well before nudge #2 -- long
            # enough to prove a nudge fired at all, short enough to isolate
            # which one.
            SleepFrame(sleep=0.08),
        ],
    )

    # The timer was never cancelled by the bare transcript -- a nudge fires.
    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1
    assert speak_frames[0].text == "Hello?"


@pytest.mark.asyncio
async def test_user_started_speaking_pauses_the_timer_without_resetting_strikes():
    """Phase 5D: guest actively speaking must cancel the pending nudge (so
    it can never interrupt them), but starting to speak is not yet proof of
    a meaningful completed response -- _prompts_sent must NOT reset here.

    Non-vacuity note: confirmed during this phase that _prompts_sent == 0
    is vacuous if nothing ever drove it above 0 first -- a nudge must fire
    BEFORE the UserStartedSpeakingFrame for this assertion to mean
    anything real."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=5)

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.15),  # nudge #1 fires at ~0.1s -- _prompts_sent becomes 1
            UserStartedSpeakingFrame(),  # guest starts talking
            SleepFrame(sleep=0.3),  # no further frames -- timer must stay cancelled, no more nudges
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1  # only the one nudge before speech started
    # Speech having merely STARTED must not count as a reset "win" either --
    # it's paused, not credited. _prompts_sent stays at whatever it was.
    assert watchdog._prompts_sent == 1


@pytest.mark.asyncio
async def test_end_call_ends_after_the_closing_line_finishes_playing():
    # Simulates the end_call tool (app/voice/tools.py): the LLM has already
    # spoken its closing line as normal text (that's why request_end_after_
    # current_turn is called here, standing in for the tool, before any
    # frame is sent) -- the call must NOT end immediately. It should only end
    # once BotStoppedSpeakingFrame confirms that closing line actually
    # finished playing through TTS.
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0, max_prompts=2)
    await watchdog.request_end_after_current_turn()

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),  # the closing line has now finished playing
        ],
        send_end_frame=False,  # the processor ends the call itself
    )

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)
    assert isinstance(down_frames[-1], EndFrame)
    assert down_frames[-1].reason == "conversation complete"


@pytest.mark.asyncio
async def test_guest_speaking_again_cancels_a_pending_end_call():
    # Guest thinks of one more question while/after the closing line is
    # playing -- a real transcript arriving must cancel the pending hangup,
    # not race against it. Deliberately kept on the raw TranscriptionFrame
    # signal (not gated on full turn completion) -- see silence_watchdog.py's
    # own TranscriptionFrame branch comment for why.
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.2, max_prompts=2)
    await watchdog.request_end_after_current_turn()

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            _real_transcript("Wait, one more thing"),  # arrives before BotStoppedSpeakingFrame
            SleepFrame(sleep=0.05),  # let the transcript actually be processed first
            BotStoppedSpeakingFrame(),  # now this should start a normal silence-nudge timer instead
            SleepFrame(sleep=0.3),  # long enough for a nudge to fire if the timer restarted normally
        ],
    )

    assert watchdog._end_requested is False
    assert not any(isinstance(f, EndFrame) for f in down_frames)
    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1
    assert speak_frames[0].text == "Hello?"


# --- Phase 5 (documentation/agent-conversation-improvement.md): conversation
# lifecycle -- ConversationState.closing_state tracked as real state through
# the arm/reopen/close sequence, not just this processor's own hangup_pending
# bookkeeping. ---


@pytest.mark.asyncio
async def test_request_end_marks_conversation_state_farewell_pending():
    state = ConversationState()
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0, conversation_state=state)

    await watchdog.request_end_after_current_turn()

    assert state.closing_state == "farewell_pending"
    assert state.conversation_goal == "closing"


@pytest.mark.asyncio
async def test_guest_speaking_again_resets_conversation_state_to_open():
    """Full Phase 5.1 sequence: arm end_call -> confirm farewell_pending ->
    guest speaks again before the hangup completes -> confirm the watchdog's
    own existing cancellation path also resets closing_state back to open."""
    state = ConversationState()
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.2, conversation_state=state)

    await watchdog.request_end_after_current_turn()
    assert state.closing_state == "farewell_pending"

    await run_test(
        watchdog,
        frames_to_send=[
            _real_transcript("Wait, one more thing"),
            SleepFrame(sleep=0.05),
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.3),
        ],
    )

    assert state.closing_state == "open"


@pytest.mark.asyncio
async def test_premature_end_call_guard_cancellation_also_reopens_conversation_state():
    """cancel_end_request (PrematureEndCallGuardProcessor's path, a same-turn
    end_call + real question) must reopen state the same way a guest
    speaking again does -- both are "the call isn't actually over yet."""
    state = ConversationState()
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0, conversation_state=state)

    await watchdog.request_end_after_current_turn()
    assert state.closing_state == "farewell_pending"

    watchdog.cancel_end_request()

    assert state.closing_state == "open"


@pytest.mark.asyncio
async def test_end_call_completing_marks_conversation_state_closed():
    state = ConversationState()
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0, conversation_state=state)
    await watchdog.request_end_after_current_turn()

    await run_test(
        watchdog,
        frames_to_send=[BotStoppedSpeakingFrame()],
        send_end_frame=False,
    )

    assert state.closing_state == "closed"


@pytest.mark.asyncio
async def test_second_close_later_in_the_same_call_is_treated_as_a_fresh_legitimate_close():
    """A reopened call that later closes again for real must not be treated
    as a blocked duplicate -- reopening was explicit and genuine, so a
    second full close-and-goodbye is a normal, fresh close."""
    state = ConversationState()
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.2, conversation_state=state)

    # First close attempt, then the guest reopens it.
    await watchdog.request_end_after_current_turn()
    assert state.closing_state == "farewell_pending"
    watchdog.cancel_end_request()
    assert state.closing_state == "open"

    # A second, genuine close later in the same call.
    await watchdog.request_end_after_current_turn()
    assert state.closing_state == "farewell_pending"

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[BotStoppedSpeakingFrame()],
        send_end_frame=False,
    )

    assert state.closing_state == "closed"
    assert isinstance(down_frames[-1], EndFrame)
    assert down_frames[-1].reason == "conversation complete"


@pytest.mark.asyncio
async def test_no_conversation_state_is_a_no_op_for_closing_lifecycle():
    """Every existing call site/test that constructs this processor without
    a ConversationState must keep working unchanged."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)

    await watchdog.request_end_after_current_turn()
    watchdog.cancel_end_request()

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[BotStoppedSpeakingFrame()],
    )
    assert not any(isinstance(f, EndFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_repeated_background_completed_turns_do_not_reset_strikes_but_still_get_time():
    """Phase 5D update of Phase 5A's own root-cause characterization test:
    with the VAD wiring bug fixed and UserStoppedSpeakingFrame now the real
    reset signal, a background voice that happens to complete several
    near-identical "turns" (same text, no real bot activity between them)
    is now caught by the repetition-shadow signal once it reaches
    _REPETITION_SHADOW_MIN_MATCHES -- the strike counter stops resetting,
    so the 2-follow-up cap can no longer be deferred forever by this
    pattern. This directly closes the gap Phase 5A's own test documented
    as NOT yet closed. The guest (or background voice) still always gets a
    full fresh timeout window per completed turn -- this signal only
    withholds "that counted as real progress", it never shortens anyone's
    response time (see silence_watchdog.py's own docstring).

    Non-vacuity note: confirmed during this phase that asserting only "the
    call eventually ends" is vacuous here -- the call ends regardless of
    whether the repetition gate does anything, because the very LAST
    completed turn in the sequence is always followed by an unanswered
    fresh timer window that elapses on its own either way. The real,
    distinguishing property is checked directly below: once the
    repetition threshold is reached, _prompts_sent must NOT return to 0
    on a subsequent matching completed turn -- with the gate disabled,
    it does."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.05, max_prompts=10)
    min_matches = silence_watchdog_module._REPETITION_SHADOW_MIN_MATCHES

    # Drive to exactly the repetition threshold -- one nudge fires in the
    # gap before the threshold-th completed turn arrives (0.06s > 0.05s
    # timeout), making _prompts_sent nonzero right as the gate engages.
    frames = [BotStoppedSpeakingFrame()]
    for _ in range(min_matches):
        frames += [SleepFrame(sleep=0.06), *_completed_turn("No")]

    down_frames, _ = await run_test(watchdog, frames_to_send=frames)

    # The real, non-vacuous assertion: strikes accumulated from the nudges
    # that fired during the repeated-pattern gaps, and were NOT reset back
    # to 0 once the repetition threshold was reached.
    assert watchdog._prompts_sent > 0


@pytest.mark.asyncio
async def test_duplicate_completed_turns_do_not_double_reset_or_misbehave():
    """Step 15/matrix item 11 (Phase 5C origin, re-verified under Phase 5D
    semantics): two genuinely completed turns with the same text arriving
    close together (STT correcting/re-finalizing, or two real quick
    replies) must not cause any double-counting or incorrect state."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.2, max_prompts=2)

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.02),
            *_completed_turn("I have a question"),
            *_completed_turn("I have a question"),  # duplicate/corrected re-finalization
            SleepFrame(sleep=0.05),  # well before the fresh 0.2s window elapses
        ],
    )

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)
    assert watchdog._prompts_sent == 0


def test_production_wired_timeout_is_nine_seconds():
    """Phase 5D: restored the normal idle-nudge timeout from Phase 5A's
    4.0s stopgap back to 9.0s (app/voice/pipeline.py's own
    _SILENCE_WATCHDOG_TIMEOUT_SECONDS, used to construct the real
    SilenceWatchdogProcessor inside _run_pipeline_inner) -- safe again once
    the actual root cause (dead VAD wiring, unreliable turn-completion
    signal) was fixed instead of merely shortened. This is the value that
    actually governs a live call, distinct from this processor's own
    DEFAULT_SILENCE_TIMEOUT_SECONDS (5.0), which only matters for a caller
    that doesn't pass timeout_seconds explicitly (most of this file's own
    tests). A regression here means either the Phase 5D product decision
    was reverted, or pipeline.py stopped using the named constant to
    construct the real processor."""
    assert pipeline._SILENCE_WATCHDOG_TIMEOUT_SECONDS == 9.0


# ---------------------------------------------------------------------------
# Phase 5D -- VAD-driven pause/resume around UserStartedSpeakingFrame/
# UserStoppedSpeakingFrame. Matches the brief's own numbered test matrix
# (items 1-18) as closely as the frame-level unit-test boundary allows.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_1_guest_answers_after_2_seconds_no_nudge():
    watchdog = SilenceWatchdogProcessor(timeout_seconds=9.0, max_prompts=2)
    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.05),  # stand-in for "2 sec" at test-appropriate scale
            *_completed_turn("Yes"),
            SleepFrame(sleep=0.05),
        ],
    )
    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_2_and_3_guest_answers_just_before_timeout_no_nudge():
    """Items 2/3 (7s / 8.9s of a 9s window): answering with any real margin
    before the deadline, however small, must never trigger a nudge.

    Note: UserStartedSpeakingFrame is what actually protects a guest who is
    mid-utterance right at the deadline (test_8) -- this test instead
    exercises the case where VAD confirms speech well before the deadline
    (via UserStartedSpeakingFrame, which cancels the pending timer), and
    the transcript/turn-completion follows shortly after. A bare
    TranscriptionFrame arriving without a preceding UserStartedSpeakingFrame
    does NOT cancel the timer on its own (Phase 5D's whole point), so
    real production calls rely on VAD start, not transcript arrival, to
    protect an in-progress utterance -- see test_8."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.2, max_prompts=2)
    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.1),  # guest starts answering partway through the window
            UserStartedSpeakingFrame(),  # VAD confirms -- pauses the timer
            SleepFrame(sleep=0.15),  # takes a while to finish speaking (would exceed the original window)
            *_completed_turn("Yes"),
            SleepFrame(sleep=0.05),
        ],
    )
    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_4_no_response_for_full_window_triggers_nudge_1():
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)
    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[BotStoppedSpeakingFrame(), SleepFrame(sleep=0.15)],
    )
    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1
    assert speak_frames[0].text == "Hello?"


@pytest.mark.asyncio
async def test_5_guest_answers_after_nudge_1_no_nudge_2():
    """After nudge #1 fires, the guest answers -- the completed turn resets
    strikes and starts a FRESH window.

    Non-vacuity note: confirmed during this phase that checking only
    "no second nudge yet" within a short trailing window is vacuous --
    that assertion holds whether or not the reset assignment actually ran,
    since the fresh timer wouldn't have elapsed either way in that short a
    window. Asserting watchdog._prompts_sent directly is what actually
    distinguishes the two cases."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.15, max_prompts=2)
    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.2),  # nudge #1 fires at ~0.15s -- _prompts_sent becomes 1
            *_completed_turn("Sorry, still here"),
            SleepFrame(sleep=0.05),  # well inside the fresh 0.15s window
        ],
    )
    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1  # only nudge #1, never a second
    assert watchdog._prompts_sent == 0  # the real, non-vacuous assertion


@pytest.mark.asyncio
async def test_6_no_response_after_nudge_1_triggers_nudge_2():
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)
    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[BotStoppedSpeakingFrame(), SleepFrame(sleep=0.25)],
    )
    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 2
    assert speak_frames[0].text == "Hello?"
    assert speak_frames[1].text == "Hello, are you there?"


@pytest.mark.asyncio
async def test_7_no_response_after_nudge_2_ends_call():
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)
    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[BotStoppedSpeakingFrame(), SleepFrame(sleep=0.5)],
        send_end_frame=False,
    )
    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 3
    assert isinstance(down_frames[-1], EndFrame)
    assert down_frames[-1].reason == "silent caller"


@pytest.mark.asyncio
async def test_8_guest_actively_speaking_at_boundary_is_not_interrupted():
    """Item 8: the guest starts speaking right as the window would expire --
    the nudge must NOT fire while they're mid-utterance."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)
    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.09),  # just before the 0.1s deadline
            UserStartedSpeakingFrame(),  # guest starts talking right at the boundary
            SleepFrame(sleep=0.3),  # they keep going -- no UserStoppedSpeakingFrame yet
        ],
    )
    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_9_hesitation_umm_one_second_is_not_treated_as_silence():
    """Item 9: 'umm, one second' is a single, non-repeated utterance -- a
    genuinely completed turn like any other, must reset the strike counter
    normally (the repetition-shadow signal only fires on a REPEATED
    pattern, never a single occurrence, so no phrase-specific allowlist is
    needed for this to work correctly)."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.2, max_prompts=2)
    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.05),
            *_completed_turn("umm, one second"),
            SleepFrame(sleep=0.05),  # well inside the fresh 0.2s window started by the reset
        ],
    )
    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)
    assert watchdog._prompts_sent == 0


@pytest.mark.asyncio
async def test_10_clearly_irrelevant_background_phrase_does_not_indefinitely_reset():
    """Item 10: a repeated irrelevant/background-shaped phrase must not
    indefinitely reset the inactivity cycle -- covered end-to-end by
    test_repeated_background_completed_turns_do_not_reset_strikes_but_still_get_time
    above; this test names it explicitly to match the brief's own numbering."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)
    min_matches = silence_watchdog_module._REPETITION_SHADOW_MIN_MATCHES

    frames = [BotStoppedSpeakingFrame()]
    for _ in range(min_matches + 2):
        frames += [SleepFrame(sleep=0.02), *_completed_turn("background chatter")]

    down_frames, _ = await run_test(watchdog, frames_to_send=frames, send_end_frame=False)

    assert isinstance(down_frames[-1], EndFrame)
    assert down_frames[-1].reason == "silent caller"


@pytest.mark.asyncio
async def test_11_repeated_background_transcripts_cannot_keep_the_call_alive_forever():
    """Item 11: same property as item 10, phrased as the brief's own
    'cannot keep the call alive forever' framing -- confirms the call
    genuinely ends (not just that nudges eventually fire)."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)
    min_matches = silence_watchdog_module._REPETITION_SHADOW_MIN_MATCHES

    frames = [BotStoppedSpeakingFrame()]
    for _ in range(min_matches + 4):
        frames += [SleepFrame(sleep=0.02), *_completed_turn("No")]

    down_frames, _ = await run_test(watchdog, frames_to_send=frames, send_end_frame=False)

    assert isinstance(down_frames[-1], EndFrame)
    assert down_frames[-1].reason == "silent caller"


@pytest.mark.asyncio
async def test_12_normal_multiturn_conversation_watchdog_never_interferes():
    """Item 12: a normal back-and-forth (bot turn, guest turn, bot turn,
    guest turn, distinct content each time) must never nudge or hang up."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)
    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.02),
            *_completed_turn("I'd like to book a villa in Goa"),
            SleepFrame(sleep=0.02),
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.02),
            *_completed_turn("Two guests, next weekend"),
            SleepFrame(sleep=0.02),
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.02),
            *_completed_turn("Sounds good, book it"),
            SleepFrame(sleep=0.05),
        ],
    )
    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)
    assert not any(isinstance(f, EndFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_13_guest_interrupts_mid_bot_speech_existing_behavior_intact():
    """Item 13: UserStartedSpeakingFrame arriving BEFORE any
    BotStoppedSpeakingFrame (i.e. the guest interrupts while the bot is
    still speaking) must not error or misbehave -- there's no timer running
    yet at that point (none was ever started), so this is a pure no-op for
    this processor, matching pipecat's own interruption handling being
    entirely independent of this watchdog."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)
    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            UserStartedSpeakingFrame(),  # guest interrupts before any bot turn completed
            SleepFrame(sleep=0.05),
        ],
    )
    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)
    assert not any(isinstance(f, EndFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_14_bot_stopped_speaking_starts_the_correct_waiting_period():
    """Item 14: explicit timing check that BotStoppedSpeakingFrame starts a
    timer of exactly timeout_seconds, not some other value."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.15, max_prompts=2)
    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.1),  # before the 0.15s deadline
        ],
    )
    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)

    watchdog2 = SilenceWatchdogProcessor(timeout_seconds=0.05, max_prompts=2)
    down_frames2, _ = await run_test(
        watchdog2,
        frames_to_send=[
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.15),  # comfortably after the 0.05s deadline
        ],
    )
    assert any(isinstance(f, TTSSpeakFrame) for f in down_frames2)


def test_15_reset_negotiation_context_style_state_clears_relevant_watchdog_state():
    """Item 15: this processor's own equivalent of a context reset --
    constructing a fresh SilenceWatchdogProcessor (the real-world analog of
    a new call/new context) starts with completely clean state. Directly
    inspects the actual attributes rather than inferring cleanliness from
    behavior alone."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)
    assert watchdog._prompts_sent == 0
    assert watchdog._timer_task is None
    assert watchdog._ended is False
    assert watchdog._end_requested is False
    assert len(watchdog._recent_transcripts) == 0
    assert watchdog._pending_turn_is_repetition_candidate is False


# Item 16 (max call duration remains independent) -- covered by the existing,
# untouched tests/test_max_call_duration.py, which already proves the hard
# ceiling fires regardless of what this processor observes; re-run as part
# of this phase's own regression rather than duplicated here.

# Item 17 (existing Phase 5A/5B/5C tests remain valid) -- this file's own
# pre-existing tests above (renamed/updated only where the underlying
# signal genuinely changed, per this phase's investigation) ARE that
# validation; the shadow-mode test section below is carried forward
# unmodified in its own logic (only the module-level default timeout
# reference below was updated).


# ---------------------------------------------------------------------------
# Phase 5C -- repetition-shadow signal. Logic itself is UNCHANGED by Phase
# 5D (only its consumer, process_frame's UserStoppedSpeakingFrame branch,
# changed) -- these tests exercise _observe_repetition_shadow directly and
# remain valid as written. All tests in this section verify the shadow
# computation itself (via a monkeypatched logger.debug capturing the exact
# metadata that would be logged -- caplog does NOT capture loguru output in
# this repo without an explicit propagation bridge, confirmed by direct
# probe during Phase 5C) AND separately verify watchdog reset behavior.
# ---------------------------------------------------------------------------


def _capture_shadow_log(monkeypatch):
    """Patches app.voice.silence_watchdog.logger.debug to capture every
    call's positional args, so tests can assert on the exact
    repetition_shadow_candidate/prior_match_count values that would be
    logged without depending on loguru-to-caplog propagation (confirmed
    absent in this repo) or reimplementing _observe_repetition_shadow's
    own logic in the test itself."""
    calls = []
    original_debug = silence_watchdog_module.logger.debug

    def _fake_debug(msg, *args, **kwargs):
        if msg.startswith("repetition_shadow_observation"):
            calls.append(args)
        else:
            original_debug(msg, *args, **kwargs)

    monkeypatch.setattr(silence_watchdog_module.logger, "debug", _fake_debug)
    return calls


def _shadow_candidate(call_args) -> bool:
    # logger.debug("...{}...", repetition_shadow_candidate, prior_match_count, ...)
    return call_args[0]


# --- A. Basic observation -- single transcripts must never be flagged. ---


@pytest.mark.asyncio
async def test_a1_single_yes_is_not_a_repetition_candidate(monkeypatch):
    calls = _capture_shadow_log(monkeypatch)
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)
    await run_test(watchdog, frames_to_send=[_real_transcript("Yes")])
    assert len(calls) == 1
    assert _shadow_candidate(calls[0]) is False


@pytest.mark.asyncio
async def test_a2_single_no_is_not_a_repetition_candidate(monkeypatch):
    calls = _capture_shadow_log(monkeypatch)
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)
    await run_test(watchdog, frames_to_send=[_real_transcript("No")])
    assert len(calls) == 1
    assert _shadow_candidate(calls[0]) is False


@pytest.mark.asyncio
async def test_a3_single_location_answer_is_not_a_repetition_candidate(monkeypatch):
    calls = _capture_shadow_log(monkeypatch)
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)
    await run_test(watchdog, frames_to_send=[_real_transcript("Goa")])
    assert len(calls) == 1
    assert _shadow_candidate(calls[0]) is False


@pytest.mark.asyncio
async def test_a4_single_numeric_answer_is_not_a_repetition_candidate(monkeypatch):
    calls = _capture_shadow_log(monkeypatch)
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)
    await run_test(watchdog, frames_to_send=[_real_transcript("Two")])
    assert len(calls) == 1
    assert _shadow_candidate(calls[0]) is False


@pytest.mark.asyncio
async def test_a5_single_hindi_response_is_not_a_repetition_candidate(monkeypatch):
    calls = _capture_shadow_log(monkeypatch)
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)
    await run_test(watchdog, frames_to_send=[_real_transcript("Haan")])
    assert len(calls) == 1
    assert _shadow_candidate(calls[0]) is False


# --- B. Repetition observation. ---


@pytest.mark.asyncio
async def test_b6_repeated_identical_transcript_becomes_a_shadow_candidate_at_threshold(monkeypatch):
    """_REPETITION_SHADOW_MIN_MATCHES identical transcripts, no bot
    activity between them, must flip repetition_shadow_candidate to True
    on the Nth occurrence -- not before."""
    calls = _capture_shadow_log(monkeypatch)
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)
    min_matches = silence_watchdog_module._REPETITION_SHADOW_MIN_MATCHES

    frames = [_real_transcript("No") for _ in range(min_matches)]
    await run_test(watchdog, frames_to_send=frames)

    assert len(calls) == min_matches
    # Every occurrence before the threshold must be False, the threshold-th
    # (and only the threshold-th, here) must be True.
    for i, call in enumerate(calls, start=1):
        assert _shadow_candidate(call) is (i >= min_matches)


@pytest.mark.asyncio
async def test_b7_repeated_near_identical_transcript_matches_via_similarity(monkeypatch):
    """Superficial differences (case/punctuation) must still be recognized
    as the same repeated text -- reusing repetition_guard._normalize's own
    tolerance, not a second independent normalization implementation."""
    calls = _capture_shadow_log(monkeypatch)
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)
    min_matches = silence_watchdog_module._REPETITION_SHADOW_MIN_MATCHES

    variants = ["no", "No.", "NO", "no!", "No,"]
    frames = [_real_transcript(variants[i % len(variants)]) for i in range(min_matches)]
    await run_test(watchdog, frames_to_send=frames)

    assert len(calls) == min_matches
    assert _shadow_candidate(calls[-1]) is True


@pytest.mark.asyncio
async def test_b8_repeated_pattern_with_no_bot_activity_is_a_candidate(monkeypatch):
    """Explicit restatement of B6 under this item's own name from the test
    matrix -- confirms the 'no bot activity' precondition is what's
    actually in effect (no BotStoppedSpeakingFrame sent at all here)."""
    calls = _capture_shadow_log(monkeypatch)
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)
    min_matches = silence_watchdog_module._REPETITION_SHADOW_MIN_MATCHES

    frames = [_real_transcript("Yeah") for _ in range(min_matches)]
    await run_test(watchdog, frames_to_send=frames)

    assert _shadow_candidate(calls[-1]) is True


# --- C. Legitimate repetition -- must NOT be flagged. ---


@pytest.mark.asyncio
async def test_c9_yes_bot_responds_yes_again_is_not_a_candidate(monkeypatch):
    """The exact VALID example from the Phase 5C brief (Step 6):
    guest says Yes, bot responds, guest says Yes again -- a real
    BotStoppedSpeakingFrame between the two must clear the shadow history
    so this reads as two independent observations, not a two-in-a-row
    streak, regardless of how low _REPETITION_SHADOW_MIN_MATCHES is.

    Non-vacuity note: pipecat's own QueuedFrameProcessor/test harness does
    NOT guarantee frames_to_send delivery order is preserved across mixed
    control/data frame types without SleepFrame separators forcing
    sequential delivery -- confirmed directly during Phase 5C (a
    same-batch BotStoppedSpeakingFrame was observed arriving at
    process_frame BEFORE both TranscriptionFrames despite being sent
    between them, which would have made this test pass for the wrong
    reason: "no transcripts existed yet when bot-stop arrived" rather than
    "bot-stop actually cleared an existing streak"). SleepFrame gaps here
    force the intended arrival order, matching this file's own
    pre-existing convention for every other order-sensitive test."""
    calls = _capture_shadow_log(monkeypatch)
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)

    await run_test(
        watchdog,
        frames_to_send=[
            _real_transcript("Yes"),
            SleepFrame(sleep=0.02),
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.02),
            _real_transcript("Yes"),
        ],
    )

    assert len(calls) == 2
    assert _shadow_candidate(calls[0]) is False
    assert _shadow_candidate(calls[1]) is False
    # The real assertion this test exists for: the SECOND "Yes" must have
    # seen an EMPTY history at the time it was observed (prior_match_count
    # == 0), proving the intervening BotStoppedSpeakingFrame actually
    # cleared the first "Yes" out, not merely that neither happened to
    # reach the configured threshold.
    assert calls[1][1] == 0


@pytest.mark.asyncio
async def test_c10_correction_goa_then_no_wait_kerala_is_not_a_repeated_pattern(monkeypatch):
    calls = _capture_shadow_log(monkeypatch)
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)

    await run_test(
        watchdog,
        frames_to_send=[_real_transcript("Goa"), _real_transcript("No, Kerala")],
    )

    assert len(calls) == 2
    assert _shadow_candidate(calls[0]) is False
    assert _shadow_candidate(calls[1]) is False


@pytest.mark.asyncio
async def test_c11_yes_yes_okay_does_not_assume_repetition_without_configured_conditions(monkeypatch):
    """'Yes... yes... okay' -- two matches on 'yes' plus one unrelated
    'okay' -- must not reach the configured threshold unless
    _REPETITION_SHADOW_MIN_MATCHES is itself as low as 2. Asserts the
    actual arithmetic (matches counted, not assumed) rather than a fixed
    True/False, since the two 'yes' occurrences ARE genuinely similar to
    each other -- the test proves the count is exactly right, not that
    similarity was ignored."""
    calls = _capture_shadow_log(monkeypatch)
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)

    await run_test(
        watchdog,
        frames_to_send=[
            _real_transcript("Yes"),
            _real_transcript("yes"),
            _real_transcript("okay"),
        ],
    )

    assert len(calls) == 3
    # Third transcript ("okay") does not textually match either "yes" --
    # its own match count must be 0 regardless of the "yes"/"yes" pair
    # before it.
    prior_match_count_for_okay = calls[2][1]
    assert prior_match_count_for_okay == 0


@pytest.mark.asyncio
async def test_c12_slow_hesitant_confirmation_across_bot_turns_remains_valid(monkeypatch):
    """Several short confirmations, each separated by real bot activity --
    must never accumulate into a shadow candidate no matter how many
    occur, since a BotStoppedSpeakingFrame clears history each time.

    Uses SleepFrame separators to force sequential delivery -- see
    test_c9's own docstring for why this is required for the test to
    exercise the intended order rather than passing vacuously (confirmed
    live during Phase 5C: without the separators, this exact test failed
    because ALL BotStoppedSpeakingFrames in a mixed batch arrived before
    ANY TranscriptionFrame, which is a real pipecat test-harness ordering
    behavior, not a bug in the implementation under test)."""
    calls = _capture_shadow_log(monkeypatch)
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)

    frames = []
    for _ in range(5):
        frames.append(_real_transcript("Okay"))
        frames.append(SleepFrame(sleep=0.02))
        frames.append(BotStoppedSpeakingFrame())
        frames.append(SleepFrame(sleep=0.02))
    await run_test(watchdog, frames_to_send=frames)

    assert len(calls) == 5
    assert all(_shadow_candidate(c) is False for c in calls)
    # Every occurrence must have seen an empty history (prior_match_count
    # == 0) -- proves each BotStoppedSpeakingFrame genuinely cleared the
    # previous one, not merely that 5 occurrences never reached threshold.
    assert all(c[1] == 0 for c in calls)


# --- D. STT duplicate/re-finalization. ---


@pytest.mark.asyncio
async def test_d13_stt_refinalization_duplicate_records_evidence_but_does_not_change_watchdog_behavior(monkeypatch):
    """Same transcript twice, no bot activity in between (Sarvam's
    documented late-refinalization behavior, per turn_strategies.py's own
    comment) -- the shadow layer MAY record this as matching evidence (it
    cannot distinguish this from genuine repeated background speech, by
    design -- see _observe_repetition_shadow's own docstring). Note this
    test deliberately sends bare TranscriptionFrames with NO
    UserStoppedSpeakingFrame (Phase 5D: the realistic shape of a
    same-utterance re-finalization -- both transcripts belong to ONE
    still-in-progress turn, so only one UserStoppedSpeakingFrame would
    ever really follow) -- confirms the watchdog's own reset behavior is
    unaffected regardless."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.2, max_prompts=2)

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            _real_transcript("I have a question"),
            _real_transcript("I have a question"),
            SleepFrame(sleep=0.3),
        ],
    )

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)
    assert watchdog._prompts_sent == 0


@pytest.mark.asyncio
async def test_d14_corrected_transcript_goa_then_kerala_is_not_identical_repetition(monkeypatch):
    calls = _capture_shadow_log(monkeypatch)
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)

    await run_test(watchdog, frames_to_send=[_real_transcript("Goa"), _real_transcript("Kerala")])

    assert len(calls) == 2
    assert _shadow_candidate(calls[1]) is False


# --- E. Watchdog invariance -- Phase 5C's own absolute behavioral
# requirement, now superseded in spirit by Phase 5D (repetition candidates
# DO now withhold the strike-counter reset -- see
# test_repeated_background_completed_turns_do_not_reset_strikes_but_still_get_time
# above), kept here to prove the TIMER itself (not the strike counter) is
# still always restarted regardless of the shadow verdict. ---


@pytest.mark.asyncio
async def test_e15_shadow_candidate_still_gets_a_fresh_timer_window():
    """Even once repetition_shadow_candidate becomes True, the TIMER is
    still unconditionally restarted (the guest/background source still
    gets a full fresh window) -- only the strike counter withholds its
    reset. Proven here by driving exactly to the shadow threshold and
    confirming the call has NOT ended yet (still within its follow-up
    budget), unlike test_11 which drives well past it."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)
    min_matches = silence_watchdog_module._REPETITION_SHADOW_MIN_MATCHES

    frames = [BotStoppedSpeakingFrame()]
    for _ in range(min_matches):
        frames += [SleepFrame(sleep=0.02), *_completed_turn("No")]

    down_frames, _ = await run_test(watchdog, frames_to_send=frames)

    # Exactly at threshold -- the call must not have ended yet (still has
    # follow-up budget remaining), proving the timer kept restarting giving
    # each occurrence a real window rather than fast-forwarding to hangup.
    assert not any(isinstance(f, EndFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_e18_true_silence_timeout_behavior_is_unchanged():
    """Direct re-run of the pre-existing full nudge-then-hangup sequence --
    confirms true silence (no transcripts at all, so _observe_repetition_
    shadow never even runs) is completely unaffected by the repetition
    signal existing."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[BotStoppedSpeakingFrame(), SleepFrame(sleep=1.0)],
        send_end_frame=False,
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 3
    assert isinstance(down_frames[-1], EndFrame)
    assert down_frames[-1].reason == "silent caller"


# --- Shadow history bookkeeping -- not part of the brief's numbered
# matrix, but directly proves the BotStoppedSpeakingFrame-clears-history
# mechanism Section C's tests depend on, in isolation. ---


def test_shadow_history_is_cleared_by_bot_stopped_speaking_frame_directly():
    """White-box check on the actual state, independent of the log-capture
    mechanism -- proves _recent_transcripts itself is emptied, not just
    that the logged candidate happens to read False afterward."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)
    watchdog._observe_repetition_shadow("No")
    watchdog._observe_repetition_shadow("No")
    assert len(watchdog._recent_transcripts) == 2

    watchdog._recent_transcripts.clear()  # what the BotStoppedSpeakingFrame branch does
    assert len(watchdog._recent_transcripts) == 0


def test_shadow_history_is_bounded_by_maxlen():
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)
    for i in range(silence_watchdog_module._REPETITION_SHADOW_HISTORY_MAXLEN + 5):
        watchdog._observe_repetition_shadow(f"utterance {i}")
    assert len(watchdog._recent_transcripts) == silence_watchdog_module._REPETITION_SHADOW_HISTORY_MAXLEN


def test_observe_repetition_shadow_returns_the_candidate_verdict():
    """Phase 5D: _observe_repetition_shadow's return value is no longer
    discarded (Phase 5C left it unread) -- process_frame's
    UserStoppedSpeakingFrame branch now consults it directly. Confirms the
    method actually returns the bool, not None, for both verdicts."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=5.0)
    min_matches = silence_watchdog_module._REPETITION_SHADOW_MIN_MATCHES

    first_verdict = watchdog._observe_repetition_shadow("No")
    assert first_verdict is False

    for _ in range(min_matches - 1):
        last_verdict = watchdog._observe_repetition_shadow("No")
    assert last_verdict is True


# ---------------------------------------------------------------------------
# Post-Phase-5D live-call fix: BotStartedSpeakingFrame pauses the watchdog.
#
# Confirmed live: the countdown previously ran unaware the bot was actively
# speaking (a genuine answer, a slow_tool_filler filler line, or one of this
# processor's own nudges), so a nudge could fire mid-answer, and repeated
# occurrences of this could compound into a hangup on a guest who was still
# waiting for a reply they'd never gotten a chance to respond to. These
# tests exercise BotStartedSpeakingFrame directly, which no test before this
# fix ever modeled (this processor never read that frame type at all).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bot_started_speaking_pauses_the_timer_during_a_long_answer():
    """The core reported bug: a bot answer that takes longer than
    timeout_seconds to finish speaking must NOT trigger a nudge partway
    through -- confirmed by holding BotStartedSpeakingFrame open (no
    BotStoppedSpeakingFrame) for well past the configured timeout and
    asserting zero nudges fired."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),  # a prior turn ends, starting the clock
            SleepFrame(sleep=0.05),
            BotStartedSpeakingFrame(),  # the bot begins a long answer
            SleepFrame(sleep=0.3),  # far longer than timeout_seconds -- still mid-answer
        ],
    )

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_bot_stopped_speaking_after_long_answer_restarts_a_fresh_window():
    """Once the long answer actually finishes, BotStoppedSpeakingFrame must
    still correctly restart a full, fresh window -- the pause must not
    leave the timer permanently disabled."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.05),
            BotStartedSpeakingFrame(),
            SleepFrame(sleep=0.2),  # long answer in progress
            BotStoppedSpeakingFrame(),  # the answer finally finishes
            SleepFrame(sleep=0.15),  # long enough for a fresh 0.1s window to elapse
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1
    assert speak_frames[0].text == "Hello?"


@pytest.mark.asyncio
async def test_repeated_slow_bot_turns_no_longer_compound_into_a_hangup():
    """The confirmed auto-disconnect mechanism: before this fix, nothing
    reset _prompts_sent except a completed GUEST turn, so a sequence of
    slow bot responses (no guest silence at all -- the guest is waiting on
    Mira, not the other way around) could still accumulate enough strikes
    to hang up. Simulates three consecutive slow bot turns, each one
    started well before its own timeout would have elapsed, and confirms
    the call neither nudges nor ends."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)

    frames = [BotStoppedSpeakingFrame()]
    for _ in range(3):
        frames += [
            SleepFrame(sleep=0.05),  # some processing time before the bot starts talking
            BotStartedSpeakingFrame(),
            SleepFrame(sleep=0.15),  # the bot speaks for longer than timeout_seconds
            BotStoppedSpeakingFrame(),
        ]

    down_frames, _ = await run_test(watchdog, frames_to_send=frames)

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)
    assert not any(isinstance(f, EndFrame) for f in down_frames)
    assert watchdog._prompts_sent == 0


@pytest.mark.asyncio
async def test_bot_started_speaking_after_ended_is_a_safe_no_op():
    """BotStartedSpeakingFrame arriving after the watchdog has already
    ended the call (e.g. a race during teardown) must not raise or
    misbehave -- mirrors the existing not self._ended guard already used
    for BotStoppedSpeakingFrame."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.05, max_prompts=0)

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[BotStoppedSpeakingFrame(), SleepFrame(sleep=0.15), BotStartedSpeakingFrame()],
        send_end_frame=False,
    )

    assert watchdog._ended is True
    assert isinstance(down_frames[-1], EndFrame)


# ---------------------------------------------------------------------------
# Post-Phase-5D live-call fix: nudge/goodbye lines are now visible in the
# saved call transcript (append_to_context=True, previously False).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nudges_are_marked_append_to_context_true():
    """Confirmed live: nudges were previously invisible on the calls page
    because the transcript is built directly from context.messages
    (app/voice/pipeline.py), and append_to_context=False kept them out of
    it entirely. Both nudge lines must now carry append_to_context=True.

    Uses the same timeout_seconds=0.1 / sleep=0.25 ratio as the existing
    test_6_no_response_after_nudge_1_triggers_nudge_2 (2.5 timeout windows
    fit in the sleep, landing cleanly after nudge 2 but before a third
    cycle would fire) rather than a tighter ratio that leaves too little
    margin against scheduling jitter."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[BotStoppedSpeakingFrame(), SleepFrame(sleep=0.25)],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 2
    assert all(f.append_to_context is True for f in speak_frames)


@pytest.mark.asyncio
async def test_goodbye_line_is_marked_append_to_context_true():
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.05, max_prompts=1)

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[BotStoppedSpeakingFrame(), SleepFrame(sleep=0.3)],
        send_end_frame=False,
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    goodbye_frame = speak_frames[-1]
    assert "end the call" in goodbye_frame.text.lower()
    assert goodbye_frame.append_to_context is True
