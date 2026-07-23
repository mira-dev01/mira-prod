import pytest
from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame
from pipecat.tests.utils import run_test

from app.voice.escalation_phrase_guard import SAFE_REPLACEMENT_TEXT, EscalationPhraseGuardProcessor


def _response(*chunks: str) -> list:
    return [LLMFullResponseStartFrame(), *[LLMTextFrame(c) for c in chunks], LLMFullResponseEndFrame()]


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
async def test_armed_rewrites_banned_phrase():
    guard = EscalationPhraseGuardProcessor()
    guard.arm()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response("One sec, ", "let me loop in the host directly!"),
    )

    text_frames = [f for f in down_frames if isinstance(f, LLMTextFrame)]
    assert len(text_frames) == 1
    assert text_frames[0].text == SAFE_REPLACEMENT_TEXT
    assert "loop" not in text_frames[0].text.lower()


@pytest.mark.asyncio
async def test_armed_leaves_clean_reply_untouched_and_disarms():
    guard = EscalationPhraseGuardProcessor()
    guard.arm()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response("Understood, the host will follow up with you shortly."),
    )

    text_frames = [f for f in down_frames if isinstance(f, LLMTextFrame)]
    assert "".join(f.text for f in text_frames) == "Understood, the host will follow up with you shortly."
    assert guard._armed is False


@pytest.mark.asyncio
async def test_empty_tool_call_only_cycle_stays_armed_for_next_cycle():
    guard = EscalationPhraseGuardProcessor()
    guard.arm()

    down_frames, _ = await run_test(
        guard,
        # A tool-call-only completion produces a Start/End cycle with no text.
        frames_to_send=[LLMFullResponseStartFrame(), LLMFullResponseEndFrame()]
        + _response("One sec, let me loop the host in!"),
    )

    text_frames = [f for f in down_frames if isinstance(f, LLMTextFrame)]
    assert len(text_frames) == 1
    assert text_frames[0].text == SAFE_REPLACEMENT_TEXT
