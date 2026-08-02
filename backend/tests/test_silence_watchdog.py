import pytest
from pipecat.frames.frames import BotStoppedSpeakingFrame, EndFrame, TranscriptionFrame, TTSSpeakFrame
from pipecat.tests.utils import SleepFrame, run_test

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
