import pytest
from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame
from pipecat.tests.utils import run_test

from app.voice.repetition_guard import RepetitionGuardProcessor


def _response(*chunks: str) -> list:
    return [LLMFullResponseStartFrame(), *[LLMTextFrame(c) for c in chunks], LLMFullResponseEndFrame()]


def _spoken_text(down_frames) -> str:
    return "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))


@pytest.mark.asyncio
async def test_normal_reply_passes_through_unmodified():
    guard = RepetitionGuardProcessor()

    down_frames, _ = await run_test(
        guard, frames_to_send=_response("Sure, the Cabana 1BHK sleeps two guests. Interested?")
    )

    assert _spoken_text(down_frames) == "Sure, the Cabana 1BHK sleeps two guests. Interested?"


@pytest.mark.asyncio
async def test_regression_near_duplicate_clarifying_question_gets_cut():
    """Regression for the exact live failure on 2026-07-27: a single
    completion paraphrased 'What's your budget per night?' many times in a
    row instead of asking it once. The sentence that first crosses the
    similarity threshold may still be forwarded (already streamed before its
    own text is judged -- see module docstring's latency trade-off), but
    nothing repeating past that point is."""
    guard = RepetitionGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response(
            "What's your budget range per night for each unit? ",
            "We'll tailor some options once I know your budget. ",
            "Could you share the budget you have in mind for each property per night? ",
            "What's your nightly budget per unit? ",
            "Once again, what is your budget for this stay? ",
        ),
    )

    text = _spoken_text(down_frames)
    # The first, real question always gets through.
    assert "budget range per night" in text
    # Whatever comes after the first detected repeat never does.
    assert "Once again" not in text


@pytest.mark.asyncio
async def test_regression_short_fragment_flood_gets_cut():
    """Regression for the live ".. .. .." degenerate-output flood -- too
    short for the word-overlap check, needs its own trigger. Streamed as many
    small chunks, matching how real token-level streaming actually arrives
    (a single giant already-concatenated frame isn't a realistic shape for
    this processor to receive, and can't be cut mid-frame -- cutting only
    ever withholds FUTURE frames)."""
    guard = RepetitionGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response("Sure.", *([" .. "] * 30)),
    )

    text = _spoken_text(down_frames)
    assert text.count("..") < 10


@pytest.mark.asyncio
async def test_differently_worded_non_repeating_questions_are_not_flagged():
    guard = RepetitionGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response(
            "How many guests will be staying? ",
            "Which dates were you thinking of? ",
            "Do you have a preferred area in Goa? ",
        ),
    )

    text = _spoken_text(down_frames)
    assert "How many guests" in text
    assert "Which dates" in text
    assert "preferred area" in text


@pytest.mark.asyncio
async def test_short_common_acknowledgements_are_not_flagged():
    """Short filler phrases like 'Got it.'/'Sure.' can legitimately repeat
    across different sentences within a reply without being a real
    degenerate loop -- only a flood of very short fragments should trigger."""
    guard = RepetitionGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response("Got it. Sure, let's find you a place. Got it, one moment please."),
    )

    text = _spoken_text(down_frames)
    assert "let's find you a place" in text
    assert "one moment please" in text


@pytest.mark.asyncio
async def test_cut_state_resets_between_responses():
    guard = RepetitionGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response("What's your budget? ", "What's your budget for the stay? ")
        + _response("Sure, how can I help you today?"),
    )

    text_frames = [f.text for f in down_frames if isinstance(f, LLMTextFrame)]
    # Second response must not be suppressed just because the first one was cut.
    assert text_frames[-1] == "Sure, how can I help you today?"
