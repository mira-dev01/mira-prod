import pytest
from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame
from pipecat.tests.utils import run_test

from app.voice.meta_commentary_guard import MetaCommentaryGuardProcessor


def _response(*chunks: str) -> list:
    return [LLMFullResponseStartFrame(), *[LLMTextFrame(c) for c in chunks], LLMFullResponseEndFrame()]


def _spoken_text(down_frames) -> str:
    return "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))


@pytest.mark.asyncio
async def test_normal_reply_passes_through_unmodified():
    guard = MetaCommentaryGuardProcessor()

    down_frames, _ = await run_test(guard, frames_to_send=_response("Sure, how can I help you today?"))

    assert _spoken_text(down_frames) == "Sure, how can I help you today?"


@pytest.mark.asyncio
async def test_regression_waiting_for_guest_response_is_dropped():
    """Regression for the exact live failure on 2026-07-27: 'May I have the
    best phone number to reach you on? (Waiting for guest response)' had the
    parenthetical spoken/shown as part of the reply."""
    guard = MetaCommentaryGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response(
            "May I have the best phone number to reach you on? ", "(Waiting for guest response)"
        ),
    )

    text = _spoken_text(down_frames)
    assert text == "May I have the best phone number to reach you on? "
    assert "Waiting" not in text


@pytest.mark.asyncio
async def test_meta_commentary_split_across_many_small_chunks_is_still_caught():
    guard = MetaCommentaryGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response("Got it. ", "(", "Wait", "ing", " for", " guest", " resp", "onse", ")"),
    )

    text = _spoken_text(down_frames)
    assert text == "Got it. "


@pytest.mark.asyncio
async def test_legitimate_parenthetical_content_is_kept():
    guard = MetaCommentaryGuardProcessor()

    down_frames, _ = await run_test(
        guard, frames_to_send=_response("The Cabana is a great fit (just two minutes from the beach).")
    )

    text = _spoken_text(down_frames)
    assert text == "The Cabana is a great fit (just two minutes from the beach)."


@pytest.mark.asyncio
async def test_unclosed_parenthetical_at_response_end_is_flushed_not_dropped():
    guard = MetaCommentaryGuardProcessor()

    down_frames, _ = await run_test(guard, frames_to_send=_response("Sure thing (that works great"))

    text = _spoken_text(down_frames)
    assert text == "Sure thing (that works great"


@pytest.mark.asyncio
async def test_state_resets_between_responses():
    guard = MetaCommentaryGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response("Question one? ", "(waiting)") + _response("Question two?"),
    )

    text_frames = [f.text for f in down_frames if isinstance(f, LLMTextFrame)]
    assert text_frames[-1] == "Question two?"
