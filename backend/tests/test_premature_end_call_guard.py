import asyncio

import pytest
from pipecat.frames.frames import (
    FunctionCallFromLLM,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

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


# --- Real pipecat frame order -------------------------------------------------
# Verified against pipecat 1.6.0 (services/openai/base_llm.py + llm_service.py):
# LLMFullResponseStartFrame -> LLMTextFrame... -> FunctionCallsStartedFrame (the
# tool runs here) -> LLMFullResponseEndFrame. The tests above send
# FunctionCallsStarted BEFORE the text, which is not what the pipeline does, so
# they passed while the guard could not see the question in production.


def _real_order(*chunks: str) -> list:
    # FunctionCallsStartedFrame is a SystemFrame, so a test harness that queues every frame at once
    # lets it overtake the text. In the live pipeline the text has already passed the guard by the time
    # the tool runs, so the sleep reproduces that.
    return [
        LLMFullResponseStartFrame(),
        *[LLMTextFrame(c) for c in chunks],
        SleepFrame(sleep=0.05),
        _end_call_started(),
        SleepFrame(sleep=0.05),
        LLMFullResponseEndFrame(),
    ]


@pytest.mark.asyncio
async def test_real_frame_order_question_then_end_call_cancels_the_hangup():
    watchdog = SilenceWatchdogProcessor()
    await watchdog.request_end_after_current_turn()
    guard = PrematureEndCallGuardProcessor(watchdog)

    await run_test(
        guard,
        frames_to_send=_real_order(
            "Exactly, about an hour and fifteen minutes from the airport.",
            " Anything else you'd like to sort out?",
            " Thanks so much for calling -- have a wonderful day!",
        ),
    )

    assert watchdog._end_requested is False


@pytest.mark.asyncio
async def test_real_frame_order_plain_goodbye_keeps_the_hangup():
    watchdog = SilenceWatchdogProcessor()
    await watchdog.request_end_after_current_turn()
    guard = PrematureEndCallGuardProcessor(watchdog)

    await run_test(guard, frames_to_send=_real_order("Thanks so much for calling -- have a wonderful day!"))

    assert watchdog._end_requested is True


@pytest.mark.asyncio
async def test_open_question_without_a_question_mark_also_cancels_the_hangup():
    # Hinglish / unpunctuated closers: the guest still has an open offer to answer.
    for text in ("Aur kuch madad chahiye to bataiye", "Is there anything else I can help you with", "और कुछ चाहिए"):
        watchdog = SilenceWatchdogProcessor()
        await watchdog.request_end_after_current_turn()
        guard = PrematureEndCallGuardProcessor(watchdog)

        await run_test(guard, frames_to_send=_real_order(text))

        assert watchdog._end_requested is False, text


@pytest.mark.asyncio
async def test_a_question_in_an_earlier_turn_does_not_cancel_a_later_goodbye():
    watchdog = SilenceWatchdogProcessor()
    guard = PrematureEndCallGuardProcessor(watchdog)

    await run_test(guard, frames_to_send=_response("Which dates were you thinking of?"))
    # In the live pipeline end_call arms during/after the goodbye reply, i.e. after that reply's
    # start frame has cleared the earlier question.
    await run_test(guard, frames_to_send=_real_order("Thanks so much for calling -- have a wonderful day!"))
    await watchdog.request_end_after_current_turn()

    assert watchdog._end_requested is True


# --- End to end on pipecat's real LLMService.run_function_calls ---------------------
# The tool runs in a background task, so end_call can arm the hangup before OR after the
# guard sees the reply's end frame. Both orders must leave a question unanswered-proof.

from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.frames.frames import LLMContextFrame  # noqa: E402
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat.services.llm_service import LLMService  # noqa: E402


class _FakeLLM(LLMService):
    """Emits the frames pipecat's base_llm does: start, text, run_function_calls, end."""

    def __init__(self, reply: str, **kwargs):
        super().__init__(**kwargs)
        self._reply = reply

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            try:
                await self.push_frame(LLMFullResponseStartFrame())
                await self.push_frame(LLMTextFrame(self._reply))
                await self.run_function_calls(
                    [FunctionCallFromLLM(function_name="end_call", tool_call_id="t1", arguments={}, context=frame.context)]
                )
            finally:
                await self.push_frame(LLMFullResponseEndFrame())
        else:
            await self.push_frame(frame, direction)


async def _run_end_to_end(reply: str, *, arm_delay: float = 0.0) -> SilenceWatchdogProcessor:
    watchdog = SilenceWatchdogProcessor()
    llm = _FakeLLM(reply)

    async def end_call(params):
        if arm_delay:
            await asyncio.sleep(arm_delay)  # the tool arms AFTER the reply's end frame has gone by
        await watchdog.request_end_after_current_turn()
        await params.result_callback("Call will end after this turn.")

    llm.register_function("end_call", end_call)
    await run_test(
        Pipeline([llm, PrematureEndCallGuardProcessor(watchdog)]),
        frames_to_send=[LLMContextFrame(LLMContext()), SleepFrame(sleep=0.3 + arm_delay)],
    )
    return watchdog


@pytest.mark.asyncio
@pytest.mark.parametrize("arm_delay", [0.0, 0.15])
async def test_end_to_end_a_question_with_end_call_never_hangs_up(arm_delay):
    watchdog = await _run_end_to_end("Anything else you'd like to sort out? Thanks for calling.", arm_delay=arm_delay)

    assert watchdog._end_requested is False


@pytest.mark.asyncio
@pytest.mark.parametrize("arm_delay", [0.0, 0.15])
async def test_end_to_end_a_plain_goodbye_still_hangs_up(arm_delay):
    watchdog = await _run_end_to_end("Thanks so much for calling -- have a wonderful day!", arm_delay=arm_delay)

    assert watchdog._end_requested is True


@pytest.mark.asyncio
async def test_a_question_in_an_earlier_reply_does_not_block_a_later_goodbye():
    watchdog = SilenceWatchdogProcessor()
    guard = PrematureEndCallGuardProcessor(watchdog)

    await run_test(guard, frames_to_send=_response("Which dates were you thinking of?"))
    assert watchdog._reply_asked_question is True
    await run_test(guard, frames_to_send=_response("Great, noted. Thanks for calling."))

    assert watchdog._reply_asked_question is False
    await watchdog.request_end_after_current_turn()
    assert watchdog._end_requested is True
