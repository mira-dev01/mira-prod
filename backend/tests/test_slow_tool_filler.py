import pytest
from pipecat.frames.frames import (
    ErrorFrame,
    FunctionCallCancelFrame,
    FunctionCallFromLLM,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    TTSSpeakFrame,
)
from pipecat.tests.utils import SleepFrame, run_test

from app.voice.slow_tool_filler import SlowToolFillerProcessor


def _started(function_name: str, tool_call_id: str = "call-1") -> FunctionCallsStartedFrame:
    return FunctionCallsStartedFrame(
        function_calls=[
            FunctionCallFromLLM(
                function_name=function_name, tool_call_id=tool_call_id, arguments={}, context=None
            )
        ]
    )


def _result(tool_call_id: str = "call-1") -> FunctionCallResultFrame:
    return FunctionCallResultFrame(
        function_name="recommend_properties",
        tool_call_id=tool_call_id,
        arguments={},
        result="ok",
    )


@pytest.mark.asyncio
async def test_speaks_filler_if_scoped_tool_still_running_after_delay():
    filler = SlowToolFillerProcessor(slow_tools={"recommend_properties"}, delay_seconds=0.1)

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("recommend_properties"),
            SleepFrame(sleep=0.2),  # comfortably past the 0.1s delay
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 1
    assert speak_frames[0].append_to_context is False


@pytest.mark.asyncio
async def test_no_filler_if_result_arrives_before_delay_elapses():
    filler = SlowToolFillerProcessor(slow_tools={"recommend_properties"}, delay_seconds=0.2)

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("recommend_properties"),
            SleepFrame(sleep=0.05),
            _result(),  # resolves well before the 0.2s delay
            SleepFrame(sleep=0.3),
        ],
    )

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_no_filler_if_call_is_cancelled_before_delay_elapses():
    filler = SlowToolFillerProcessor(slow_tools={"recommend_properties"}, delay_seconds=0.2)

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("recommend_properties"),
            SleepFrame(sleep=0.05),
            FunctionCallCancelFrame(function_name="recommend_properties", tool_call_id="call-1"),
            SleepFrame(sleep=0.3),
        ],
    )

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_unscoped_tool_never_triggers_a_filler():
    filler = SlowToolFillerProcessor(slow_tools={"recommend_properties"}, delay_seconds=0.05)

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("get_pricing"),
            SleepFrame(sleep=0.2),
        ],
    )

    assert not any(isinstance(f, TTSSpeakFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_a_tool_call_that_errors_out_does_not_permanently_block_later_fillers():
    # llm_service.py's run_function_calls catches an unhandled exception from
    # the tool handler and pushes an ErrorFrame instead of ever calling
    # result_callback -- confirmed live, recommend_properties has thrown in
    # production (an unexpected-keyword-argument error). No
    # FunctionCallResultFrame or FunctionCallCancelFrame follows in that
    # case, so tracking must not get stuck waiting for one forever.
    filler = SlowToolFillerProcessor(slow_tools={"recommend_properties"}, delay_seconds=0.05)

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("recommend_properties", tool_call_id="call-1"),
            SleepFrame(sleep=0.1),  # filler #1 fires; call-1 never resolves
            ErrorFrame(error="boom", fatal=False),
            _started("recommend_properties", tool_call_id="call-2"),  # a later, genuinely new call
            SleepFrame(sleep=0.1),
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert len(speak_frames) == 2


@pytest.mark.asyncio
async def test_fillers_rotate_across_multiple_calls_in_the_same_call_session():
    filler = SlowToolFillerProcessor(
        slow_tools={"recommend_properties"},
        delay_seconds=0.05,
        filler_texts=["First line.", "Second line."],
    )

    down_frames, _ = await run_test(
        filler,
        frames_to_send=[
            _started("recommend_properties", tool_call_id="call-1"),
            SleepFrame(sleep=0.1),
            _result(tool_call_id="call-1"),
            # A brief gap so the result frame is actually processed (clearing
            # _pending_tool_call_id) before the next call starts -- without
            # it, both frames get queued back-to-back with no intervening
            # await and can race.
            SleepFrame(sleep=0.02),
            _started("recommend_properties", tool_call_id="call-2"),
            SleepFrame(sleep=0.1),
        ],
    )

    speak_frames = [f for f in down_frames if isinstance(f, TTSSpeakFrame)]
    assert [f.text for f in speak_frames] == ["First line.", "Second line."]
