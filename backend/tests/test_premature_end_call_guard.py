import pytest
from pipecat.frames.frames import (
    FunctionCallFromLLM,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.tests.utils import run_test

from app.voice.premature_end_call_guard import PrematureEndCallGuardProcessor
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
async def test_end_call_with_no_question_does_not_cancel():
    watchdog = SilenceWatchdogProcessor()
    await watchdog.request_end_after_current_turn()
    guard = PrematureEndCallGuardProcessor(watchdog)

    await run_test(
        guard,
        frames_to_send=[_end_call_started()] + _response("Thanks so much for calling -- have a wonderful day!"),
    )

    assert watchdog._end_requested is True


@pytest.mark.asyncio
async def test_regression_end_call_with_a_question_cancels_the_hangup():
    """Regression for the exact live failure on 2026-07-26: one turn asked
    "Anything else you'd like to sort out?" and also called end_call,
    hanging up before the guest could answer."""
    watchdog = SilenceWatchdogProcessor()
    await watchdog.request_end_after_current_turn()
    guard = PrematureEndCallGuardProcessor(watchdog)

    await run_test(
        guard,
        frames_to_send=[_end_call_started()]
        + _response(
            "Exactly, about an hour and fifteen minutes from the airport.",
            " Anything else you'd like to sort out?",
            " Thanks so much for calling -- have a wonderful day!",
        ),
    )

    assert watchdog._end_requested is False


@pytest.mark.asyncio
async def test_a_different_tool_call_is_ignored():
    watchdog = SilenceWatchdogProcessor()
    await watchdog.request_end_after_current_turn()
    guard = PrematureEndCallGuardProcessor(watchdog)

    await run_test(
        guard,
        frames_to_send=[_other_call_started()] + _response("Anything else I can help you with today?"),
    )

    # No end_call in this turn -- the guard has nothing to do with whatever
    # end-of-call state already existed, and must leave it untouched.
    assert watchdog._end_requested is True


@pytest.mark.asyncio
async def test_text_is_never_modified():
    watchdog = SilenceWatchdogProcessor()
    guard = PrematureEndCallGuardProcessor(watchdog)
    original = "Anything else you'd like to sort out?"

    down_frames, _ = await run_test(guard, frames_to_send=[_end_call_started()] + _response(original))

    text_frames = [f for f in down_frames if isinstance(f, LLMTextFrame)]
    assert "".join(f.text for f in text_frames) == original
