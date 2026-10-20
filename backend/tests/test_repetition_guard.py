import pytest
from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame
from pipecat.tests.utils import run_test

from app.voice.conversation_state import ConversationState
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


@pytest.mark.asyncio
async def test_final_sentence_with_no_trailing_text_is_still_recorded_for_structured_repeat():
    """Regression found while building Phase 4.2: a response whose final
    sentence has no trailing text after it (the common case -- a reply that
    just ends, e.g. "That comes to ₹18,700 total for Ocean View Villa.")
    previously left that sentence sitting in _sentence_buffer forever,
    un-judged and un-recorded -- _consume only ever judges parts[:-1]. This
    matters specifically for Phase 4.2's structured cross-turn check
    (_spoken_facts persists across responses, unlike _seen_sentences, which
    intentionally resets -- see test_cut_state_resets_between_responses):
    without this fix, the single most common real shape (a price quote as
    the ENTIRE/final sentence of a reply) would never register as
    "already spoken" at all, silently defeating the whole point of 4.2 for
    the realistic case, not just an edge case."""
    state = ConversationState()
    state.record_quoted_price("Ocean View Villa", "2026-08-10", "2026-08-12", 18700)
    guard = RepetitionGuardProcessor(state)

    # Turn 1: the quote as the ENTIRE response, no trailing text after it --
    # exactly the shape that previously went unrecorded.
    await run_test(
        guard,
        frames_to_send=_response("That comes to ₹18,700 total for Ocean View Villa."),
    )
    assert guard._spoken_facts, "the final/only sentence of turn 1 must have been recorded"

    # Turn 2 (a separate, later response): reworded restatement of the same
    # fact. Must be caught -- proving turn 1's final sentence really was
    # recorded via the LLMFullResponseEndFrame flush, not silently dropped.
    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response(
            "Just to confirm, Ocean View Villa comes to ₹18,700 for your stay. ",
            "This should not be heard.",
        ),
    )

    text = _spoken_text(down_frames)
    assert "This should not be heard" not in text


@pytest.mark.asyncio
async def test_first_utterance_of_a_quoted_price_is_never_cut():
    """The tool result actually being spoken for the first time (required by
    GOLDEN_RULES) must never be treated as a repeat, even though the exact
    fact is already sitting in ConversationState.quoted_price by the time
    this response streams -- state.quoted_price is set the moment
    get_pricing fires, one turn BEFORE the model actually says the number."""
    state = ConversationState()
    state.record_quoted_price("Ocean View Villa", "2026-08-10", "2026-08-12", 18700)
    guard = RepetitionGuardProcessor(state)

    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response("That comes to ₹18,700 total for Ocean View Villa, all inclusive."),
    )

    text = _spoken_text(down_frames)
    assert "18,700" in text


@pytest.mark.asyncio
async def test_reworded_repeat_of_the_same_quoted_price_across_turns_is_cut():
    """Phase 4.2 (documentation/agent-conversation-improvement.md):
    reproduces the plan's own example -- 'As I mentioned, the villa in Goa
    is available' vs. an earlier 'The Ocean View villa is open for those
    dates' -- low word overlap, same fact repeated. Here: the SAME price
    restated in different words a turn later, with nothing new asked."""
    state = ConversationState()
    state.record_quoted_price("Ocean View Villa", "2026-08-10", "2026-08-12", 18700)
    guard = RepetitionGuardProcessor(state)

    # Turn 1: the real, first utterance of the quote.
    await run_test(
        guard,
        frames_to_send=_response("That comes to ₹18,700 total for Ocean View Villa, all inclusive."),
    )

    # Turn 2 (a LATER, separate response): reworded restatement of the exact
    # same fact, unprompted -- must be caught even though word overlap with
    # turn 1's sentence is low (this is exactly what the within-response
    # check in trigger 1 cannot see, since _seen_sentences reset between
    # responses).
    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response(
            "Just to confirm, Ocean View Villa comes to ₹18,700 for your stay. ",
            "This should not be heard.",
        ),
    )

    text = _spoken_text(down_frames)
    assert "This should not be heard" not in text


@pytest.mark.asyncio
async def test_different_price_for_same_property_is_not_flagged_as_repeat():
    """A DIFFERENT number (e.g. a discount re-quote, or a different date
    range) is real new information and must never be treated as a repeat --
    only restating the EXACT same figure is a repeat signal."""
    state = ConversationState()
    state.record_quoted_price("Ocean View Villa", "2026-08-10", "2026-08-12", 18700)
    guard = RepetitionGuardProcessor(state)

    await run_test(
        guard,
        frames_to_send=_response("That comes to ₹18,700 total for Ocean View Villa, all inclusive."),
    )

    # A guest pushed back and a discount was applied -- a genuinely new number.
    state.record_quoted_price("Ocean View Villa", "2026-08-10", "2026-08-12", 16000)
    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response("With the discount, Ocean View Villa comes to ₹16,000 total."),
    )

    text = _spoken_text(down_frames)
    assert "16,000" in text


@pytest.mark.asyncio
async def test_mentioning_the_same_property_name_for_an_unrelated_reason_is_not_flagged():
    """The guard must not become 'never say this property's name twice' --
    a legitimate second mention (e.g. confirming a booking) that doesn't
    restate the exact quoted price must pass through untouched."""
    state = ConversationState()
    state.record_quoted_price("Ocean View Villa", "2026-08-10", "2026-08-12", 18700)
    guard = RepetitionGuardProcessor(state)

    await run_test(
        guard,
        frames_to_send=_response("That comes to ₹18,700 total for Ocean View Villa, all inclusive."),
    )

    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response("Great, I've noted Ocean View Villa for your booking under that name."),
    )

    text = _spoken_text(down_frames)
    assert "noted Ocean View Villa for your booking" in text


@pytest.mark.asyncio
async def test_no_conversation_state_is_a_no_op_for_structured_check():
    """Every existing call site/test that constructs this processor without
    a ConversationState must keep working unchanged -- the structured
    cross-turn check is simply skipped."""
    guard = RepetitionGuardProcessor()

    await run_test(
        guard,
        frames_to_send=_response("That comes to ₹18,700 total for Ocean View Villa, all inclusive."),
    )
    down_frames, _ = await run_test(
        guard,
        frames_to_send=_response("Just to confirm, Ocean View Villa comes to ₹18,700 for your stay."),
    )

    text = _spoken_text(down_frames)
    assert "18,700" in text
