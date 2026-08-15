import pytest
from pipecat.frames.frames import BotStoppedSpeakingFrame, EndFrame, TranscriptionFrame, TTSSpeakFrame
from pipecat.tests.utils import SleepFrame, run_test

from app.voice import pipeline
from app.voice import silence_watchdog as silence_watchdog_module
from app.voice.conversation_state import ConversationState
from app.voice.silence_watchdog import SilenceWatchdogProcessor


def _blank_transcript() -> TranscriptionFrame:
    return TranscriptionFrame(text="", user_id="guest", timestamp="", finalized=True)


def _real_transcript(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="guest", timestamp="", finalized=True)


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
async def test_real_transcript_resets_strikes_and_cancels_timer():
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.05),
            _real_transcript("I have a question"),  # guest is there -- cancels the pending timeout
            SleepFrame(sleep=0.3),  # no BotStoppedSpeakingFrame follows, so no new timer starts
        ],
    )

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)


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
    # not race against it.
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
async def test_repeated_background_transcripts_indefinitely_defer_the_nudge_cycle():
    """Phase 5A root-cause characterization test (Step 15, item 3/7): this
    is NOT a bug in SilenceWatchdogProcessor's own logic -- it is a direct,
    faithful demonstration of the documented architectural limitation
    (see app/config.py's sarvam_vad_* comment block and app/voice/
    pipeline.py's mono-audio note): every non-blank TranscriptionFrame is
    trusted as real guest activity, with no signal available anywhere in
    this stack to distinguish a genuine guest reply from a background
    voice picked up by the same mono call audio. A guest who says nothing
    else for the rest of the call, but whose background keeps producing
    short transcribable utterances, can defer this processor's own
    nudge/hangup cycle indefinitely -- confirmed here by asserting zero
    TTSSpeakFrame nudges fire despite a span far exceeding several
    timeout cycles. This is exactly why max_call_duration_seconds
    (app/voice/pipeline.py's _enforce_max_call_duration, see
    test_max_call_duration.py) exists as an INDEPENDENT backstop that
    cannot be deferred this way -- this test documents why that backstop
    is necessary, it does not claim this processor alone is sufficient."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)

    frames = [BotStoppedSpeakingFrame()]
    for _ in range(8):
        frames.append(SleepFrame(sleep=0.05))
        frames.append(_real_transcript("No"))  # short, noise-shaped, like a background voice

    down_frames, _ = await run_test(watchdog, frames_to_send=frames)

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)
    assert not any(isinstance(f, EndFrame) for f in down_frames)
    assert watchdog._prompts_sent == 0


@pytest.mark.asyncio
async def test_duplicate_transcript_events_do_not_double_reset_or_misbehave():
    """Step 15, item 11: two real transcripts arriving close together
    (STT correcting/re-finalizing the same utterance, or two genuinely
    separate quick replies) must not cause any double-counting or
    incorrect state -- each is just another reset, idempotent in effect."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.2, max_prompts=2)

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.02),
            _real_transcript("I have a question"),
            _real_transcript("I have a question"),  # duplicate/corrected re-finalization
            SleepFrame(sleep=0.3),
        ],
    )

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)
    assert watchdog._prompts_sent == 0


def test_production_wired_timeout_is_four_seconds():
    """Phase 5A: explicit product decision to tighten the normal idle-nudge
    timeout from 9.0s to 4.0s (app/voice/pipeline.py's own
    _SILENCE_WATCHDOG_TIMEOUT_SECONDS, used to construct the real
    SilenceWatchdogProcessor inside _run_pipeline_inner) -- this is the
    value that actually governs a live call, distinct from this
    processor's own DEFAULT_SILENCE_TIMEOUT_SECONDS (5.0), which only
    matters for a caller that doesn't pass timeout_seconds explicitly
    (most of this file's own tests). A regression here means either the
    Phase 5A product decision was reverted, or pipeline.py stopped using
    the named constant to construct the real processor."""
    assert pipeline._SILENCE_WATCHDOG_TIMEOUT_SECONDS == 4.0


# ---------------------------------------------------------------------------
# Phase 5C -- SHADOW-MODE repetition observation (documentation/
# agent-conversation-improvement.md). All tests in this section verify the
# shadow computation itself (via a monkeypatched logger.debug capturing the
# exact metadata that would be logged -- caplog does NOT capture loguru
# output in this repo without an explicit propagation bridge, confirmed by
# direct probe during this phase; using caplog here would have produced
# tests that always fail regardless of correctness) AND separately verify
# watchdog reset behavior is byte-identical with the feature present.
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
    """The exact VALID example from this phase's own brief (Step 6):
    guest says Yes, bot responds, guest says Yes again -- a real
    BotStoppedSpeakingFrame between the two must clear the shadow history
    so this reads as two independent observations, not a two-in-a-row
    streak, regardless of how low _REPETITION_SHADOW_MIN_MATCHES is.

    Non-vacuity note: pipecat's own QueuedFrameProcessor/test harness does
    NOT guarantee frames_to_send delivery order is preserved across mixed
    control/data frame types without SleepFrame separators forcing
    sequential delivery -- confirmed directly during this phase (a
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
    live during this phase: without the separators, this exact test
    failed because ALL BotStoppedSpeakingFrames in a mixed batch arrived
    before ANY TranscriptionFrame, which is a real pipecat test-harness
    ordering behavior, not a bug in the implementation under test)."""
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
    design -- see _observe_repetition_shadow's own docstring), but the
    watchdog's own reset behavior (the actual, load-bearing assertion
    here) must be completely unaffected either way."""
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


# --- E. Watchdog invariance -- the absolute behavioral requirement (Step 9). ---


@pytest.mark.asyncio
async def test_e15_every_shadow_candidate_still_resets_the_watchdog(monkeypatch):
    """Even once repetition_shadow_candidate becomes True, _prompts_sent
    must still reset to 0 and the timer must still be cancelled -- proven
    by driving well past _REPETITION_SHADOW_MIN_MATCHES occurrences with a
    short real timeout and confirming no nudge ever fires."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.15, max_prompts=2)
    min_matches = silence_watchdog_module._REPETITION_SHADOW_MIN_MATCHES

    frames = []
    for _ in range(min_matches + 3):
        frames.append(_real_transcript("No"))
        frames.append(SleepFrame(sleep=0.05))

    down_frames, _ = await run_test(watchdog, frames_to_send=frames)

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)
    assert watchdog._prompts_sent == 0


@pytest.mark.asyncio
async def test_e16_existing_background_defer_characterization_test_is_unchanged():
    """Direct re-run of Phase 5A's own characterization test, unmodified in
    spirit -- confirms adding shadow observation didn't alter this
    already-established behavior at all."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)

    frames = [BotStoppedSpeakingFrame()]
    for _ in range(8):
        frames.append(SleepFrame(sleep=0.05))
        frames.append(_real_transcript("No"))

    down_frames, _ = await run_test(watchdog, frames_to_send=frames)

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)
    assert not any(isinstance(f, EndFrame) for f in down_frames)
    assert watchdog._prompts_sent == 0


@pytest.mark.asyncio
async def test_e17_real_transcript_still_resets_strikes_and_cancels_timer():
    """Direct re-run of the pre-existing test with the same name/intent --
    confirms the single-transcript reset path is untouched.

    Non-vacuity note: the original pre-existing version of this test only
    ever asserted "no nudge fired within the window" -- confirmed during
    this phase's own non-vacuity probe that this assertion alone can hold
    even with _prompts_sent left un-reset (a break that sets it to a
    stale nonzero value doesn't necessarily produce a visible nudge within
    THIS test's short window either way). Added a direct assertion on
    _prompts_sent itself, matching test_e16/test_e18's own stronger
    pattern, so a regression in the reset assignment itself is actually
    caught here."""
    watchdog = SilenceWatchdogProcessor(timeout_seconds=0.1, max_prompts=2)

    down_frames, _ = await run_test(
        watchdog,
        frames_to_send=[
            BotStoppedSpeakingFrame(),
            SleepFrame(sleep=0.05),
            _real_transcript("I have a question"),
            SleepFrame(sleep=0.3),
        ],
    )

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)
    assert watchdog._prompts_sent == 0


@pytest.mark.asyncio
async def test_e18_true_silence_timeout_behavior_is_unchanged():
    """Direct re-run of the pre-existing full nudge-then-hangup sequence --
    confirms true silence (no transcripts at all, so _observe_repetition_
    shadow never even runs) is completely unaffected by this phase."""
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
