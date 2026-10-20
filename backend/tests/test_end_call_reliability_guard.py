import pytest
from pipecat.frames.frames import (
    FunctionCallFromLLM,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.tests.utils import run_test

from app.prompts.system_prompt import DEFAULT_CLOSING_PHRASE
from app.voice.end_call_reliability_guard import EndCallReliabilityGuardProcessor
from app.voice.silence_watchdog import SilenceWatchdogProcessor


def _response(*chunks: str) -> list:
    return [LLMFullResponseStartFrame(), *[LLMTextFrame(c) for c in chunks], LLMFullResponseEndFrame()]


def _end_call_started() -> FunctionCallsStartedFrame:
    return FunctionCallsStartedFrame(
        function_calls=[FunctionCallFromLLM(function_name="end_call", tool_call_id="tc_1", arguments={}, context=None)]
    )


def _other_call_started() -> FunctionCallsStartedFrame:
    return FunctionCallsStartedFrame(
        function_calls=[FunctionCallFromLLM(function_name="update_lead", tool_call_id="tc_2", arguments={}, context=None)]
    )


@pytest.mark.asyncio
async def test_closing_line_without_end_call_arms_the_hangup_anyway():
    """The reported bug: model speaks the closing line but never calls
    end_call -- the guard must arm the hangup itself."""
    watchdog = SilenceWatchdogProcessor()
    guard = EndCallReliabilityGuardProcessor(watchdog)

    await run_test(guard, frames_to_send=_response(DEFAULT_CLOSING_PHRASE))

    assert watchdog._end_requested is True


@pytest.mark.asyncio
async def test_closing_line_with_end_call_does_not_double_arm():
    """The working case (e.g. "bye"): end_call's own tool wrapper already
    armed the watchdog this turn -- the guard must not interfere. Arming via
    request_end_after_current_turn (not just sending the started-frame)
    mirrors what tools.py's end_call wrapper actually does, same setup as
    test_premature_end_call_guard.py's regression tests."""
    watchdog = SilenceWatchdogProcessor()
    await watchdog.request_end_after_current_turn()
    guard = EndCallReliabilityGuardProcessor(watchdog)

    await run_test(guard, frames_to_send=[_end_call_started()] + _response(DEFAULT_CLOSING_PHRASE))

    assert watchdog._end_requested is True


@pytest.mark.asyncio
async def test_normal_reply_without_closing_line_is_untouched():
    watchdog = SilenceWatchdogProcessor()
    guard = EndCallReliabilityGuardProcessor(watchdog)

    await run_test(guard, frames_to_send=_response("Sure, let me check that for you."))

    assert watchdog._end_requested is False


@pytest.mark.asyncio
async def test_other_tool_call_with_no_closing_line_is_untouched():
    watchdog = SilenceWatchdogProcessor()
    guard = EndCallReliabilityGuardProcessor(watchdog)

    await run_test(
        guard,
        frames_to_send=[_other_call_started()] + _response("Got it, noted your dates."),
    )

    assert watchdog._end_requested is False


@pytest.mark.asyncio
async def test_closing_line_split_across_multiple_text_frames_still_matches():
    watchdog = SilenceWatchdogProcessor()
    guard = EndCallReliabilityGuardProcessor(watchdog)

    await run_test(
        guard,
        frames_to_send=_response("You're very welcome! ", "Thanks so much for calling", " -- have a wonderful day!"),
    )

    assert watchdog._end_requested is True


@pytest.mark.asyncio
async def test_text_is_never_modified():
    watchdog = SilenceWatchdogProcessor()
    guard = EndCallReliabilityGuardProcessor(watchdog)
    original = "Sure, let me check that for you."

    down_frames, _ = await run_test(guard, frames_to_send=_response(original))

    text_frames = [f for f in down_frames if isinstance(f, LLMTextFrame)]
    assert "".join(f.text for f in text_frames) == original


@pytest.mark.asyncio
async def test_end_call_flag_does_not_leak_into_the_next_turn():
    """end_call fires on turn 1 (closing line spoken -- fine); turn 2 is a
    normal reply with no closing line and no end_call -- must not be
    misdiagnosed as a missed end_call just because a prior turn had one."""
    watchdog = SilenceWatchdogProcessor()
    await watchdog.request_end_after_current_turn()
    guard = EndCallReliabilityGuardProcessor(watchdog)

    await run_test(
        guard,
        frames_to_send=[_end_call_started()]
        + _response(DEFAULT_CLOSING_PHRASE)
        + _response("Sure, one more thing I can help with."),
    )

    # The second turn alone (no closing line, no end_call) must not have
    # re-armed anything on its own -- _end_requested reflects turn 1's
    # legitimate arm, not a false positive from turn 2.
    assert watchdog._end_requested is True
