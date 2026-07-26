import pytest
from pipecat.frames.frames import (
    FunctionCallFromLLM,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.tests.utils import run_test

from app.voice.escalation_phrase_guard import SAFE_REPLACEMENT_TEXT, EscalationPhraseGuardProcessor


def _response(*chunks: str) -> list:
    return [LLMFullResponseStartFrame(), *[LLMTextFrame(c) for c in chunks], LLMFullResponseEndFrame()]


def _escalate_call_started() -> FunctionCallsStartedFrame:
    return FunctionCallsStartedFrame(
        function_calls=[
            FunctionCallFromLLM(
                function_name="escalate_to_host", tool_call_id="tc_1", arguments={}, context=None
            )
        ]
    )


def _other_call_started() -> FunctionCallsStartedFrame:
    return FunctionCallsStartedFrame(
        function_calls=[FunctionCallFromLLM(function_name="update_lead", tool_call_id="tc_2", arguments={}, context=None)]
    )


@pytest.mark.asyncio
async def test_unarmed_passes_banned_phrase_through_unchanged():
    guard = EscalationPhraseGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response("One sec, let me loop in the host directly!"),
    )

    text_frames = [f for f in down_frames if isinstance(f, LLMTextFrame)]
    assert "".join(f.text for f in text_frames) == "One sec, let me loop in the host directly!"


@pytest.mark.asyncio
async def test_a_different_tool_call_does_not_arm_the_guard():
    guard = EscalationPhraseGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_other_call_started()] + _response("One sec, let me loop in the host directly!"),
    )

    text_frames = [f for f in down_frames if isinstance(f, LLMTextFrame)]
    assert "".join(f.text for f in text_frames) == "One sec, let me loop in the host directly!"


@pytest.mark.asyncio
async def test_escalate_to_host_call_started_arms_and_rewrites_banned_phrase():
    guard = EscalationPhraseGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_escalate_call_started()] + _response("One sec, ", "let me loop in the host directly!"),
    )

    text_frames = [f for f in down_frames if isinstance(f, LLMTextFrame)]
    assert len(text_frames) == 1
    assert text_frames[0].text == SAFE_REPLACEMENT_TEXT
    assert "loop" not in text_frames[0].text.lower()


@pytest.mark.asyncio
async def test_same_completion_race_is_caught():
    """Regression for the exact live failure on 2026-07-26: the model puts
    the escalate_to_host tool call and the banned text in the SAME
    completion, so FunctionCallsStartedFrame and the response's own
    LLMFullResponseStartFrame/LLMTextFrame arrive back-to-back with no gap.
    A previous version armed via an external arm() call made from inside
    the tool's own Python handler -- racing against this exact frame
    sequence and losing. Sending them with no delay in between here proves
    the frame-stream-based arming has no such race."""
    guard = EscalationPhraseGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[
            _escalate_call_started(),
            LLMFullResponseStartFrame(),
            LLMTextFrame("One sec, let me loop in the host directly!"),
            LLMFullResponseEndFrame(),
        ],
    )

    text_frames = [f for f in down_frames if isinstance(f, LLMTextFrame)]
    assert len(text_frames) == 1
    assert text_frames[0].text == SAFE_REPLACEMENT_TEXT


@pytest.mark.asyncio
async def test_armed_leaves_clean_reply_untouched_and_disarms():
    guard = EscalationPhraseGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_escalate_call_started()]
        + _response("Understood, the host will follow up with you shortly."),
    )

    text_frames = [f for f in down_frames if isinstance(f, LLMTextFrame)]
    assert "".join(f.text for f in text_frames) == "Understood, the host will follow up with you shortly."
    assert guard._armed is False


@pytest.mark.asyncio
async def test_empty_tool_call_only_cycle_stays_armed_for_next_cycle():
    guard = EscalationPhraseGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_escalate_call_started(), LLMFullResponseStartFrame(), LLMFullResponseEndFrame()]
        + _response("One sec, let me loop the host in!"),
    )

    text_frames = [f for f in down_frames if isinstance(f, LLMTextFrame)]
    assert len(text_frames) == 1
    assert text_frames[0].text == SAFE_REPLACEMENT_TEXT
