"""Covers Phase 4.3 (documentation/agent-conversation-improvement.md):
ResponseShapeValidatorProcessor -- a final structural gate right before TTS,
catching malformed/concatenated response shapes that RepetitionGuardProcessor's
word-overlap check structurally cannot see (different sentences glued
together have low word overlap by construction).
"""

import pytest
from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame
from pipecat.tests.utils import run_test

from app.voice.response_shape_guard import (
    ResponseShapeValidatorProcessor,
    count_greeting_openers,
    count_questions,
    ends_mid_clause,
    first_clean_sentence_or_original,
    has_duplicated_punctuation,
    has_duplicated_safe_line,
    has_multiple_recommendation_blocks,
    has_multiple_unconnected_questions,
    validate_response_shape,
)

# Catalogue item C3 (Phase 0.2), verbatim from the real transcript (call
# d5a808a4…, 2026-07-31): three questions concatenated with no guest turn in
# between, including a real "no space after '?'" artifact -- confirmed live,
# not a synthetic worst case.
_C3_TEXT = (
    "Which one sounds interesting?Got it, Abhaya. Which of those two would you like to explore further?"
    "We'll check availability and pricing for the one you choose. Which property would you like to go ahead with?"
)


def _response(*chunks: str) -> list:
    return [LLMFullResponseStartFrame(), *[LLMTextFrame(c) for c in chunks], LLMFullResponseEndFrame()]


def _spoken_text(down_frames) -> str:
    return "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))


# --- Unit tests for the pure detection functions ---


def test_has_multiple_unconnected_questions_catches_real_c3_shape():
    assert has_multiple_unconnected_questions(_C3_TEXT) is True


def test_has_multiple_unconnected_questions_allows_single_followup_question():
    """GOLDEN_RULES already permits a single natural follow-up -- two
    questions total (routine) must not be flagged, only three or more."""
    text = "Great, I'll check that for you. Which dates were you thinking of?"
    assert has_multiple_unconnected_questions(text) is False


def test_has_multiple_unconnected_questions_allows_connected_two_part_question():
    text = "Which area are you looking at, and how many guests will be staying?"
    assert has_multiple_unconnected_questions(text) is False


def test_count_greeting_openers_catches_two_greetings():
    text = "Hi there! Namaste, welcome to our property."
    assert count_greeting_openers(text) >= 2


def test_count_greeting_openers_one_greeting_is_fine():
    text = "Hi there, how can I help you today?"
    assert count_greeting_openers(text) == 1


def test_has_duplicated_safe_line_catches_repeat():
    from app.voice.escalation_phrase_guard import SAFE_REPLACEMENT_TEXT

    text = f"{SAFE_REPLACEMENT_TEXT} {SAFE_REPLACEMENT_TEXT}"
    assert has_duplicated_safe_line(text) is True


def test_has_duplicated_safe_line_single_occurrence_is_fine():
    from app.voice.escalation_phrase_guard import SAFE_REPLACEMENT_TEXT

    assert has_duplicated_safe_line(SAFE_REPLACEMENT_TEXT) is False


def test_has_duplicated_punctuation_catches_degenerate_flood():
    """Catalogue item H2's exact mechanical shape -- belt-and-suspenders
    with RepetitionGuardProcessor's own fragment-flood detection."""
    assert has_duplicated_punctuation("Sure.. .. .. .. .. ..") is True


def test_has_duplicated_punctuation_normal_text_is_fine():
    assert has_duplicated_punctuation("That sounds great, thank you!") is False


def test_ends_mid_clause_catches_no_terminal_punctuation():
    assert ends_mid_clause("So the total comes to about") is True


def test_ends_mid_clause_catches_dangling_conjunction():
    assert ends_mid_clause("The villa has a pool and") is True


def test_ends_mid_clause_allows_normal_complete_sentence():
    assert ends_mid_clause("The villa has a private pool.") is False


def test_ends_mid_clause_allows_question_ending_in_preposition():
    """Confirmed against catalogue item C3's own real text -- 'go ahead
    with?' is a complete, valid spoken question; prepositions are common,
    legitimate sentence-final words and must never be flagged."""
    assert ends_mid_clause("Which property would you like to go ahead with?") is False


def test_has_multiple_recommendation_blocks_catches_two_intros():
    text = (
        "This one's a great fit: Ocean View Villa, sleeps 6. "
        "I have a couple of options that could work well: Palm Retreat, sleeps 4."
    )
    assert has_multiple_recommendation_blocks(text) is True


def test_has_multiple_recommendation_blocks_single_block_is_fine():
    text = "This one's a great fit: Ocean View Villa, sleeps 6."
    assert has_multiple_recommendation_blocks(text) is False


def test_validate_response_shape_clean_response_has_no_violations():
    assert validate_response_shape("How many guests will be staying with you?") == []


def test_first_clean_sentence_extracts_only_first_question_from_real_c3_text():
    assert first_clean_sentence_or_original(_C3_TEXT) == "Which one sounds interesting?"


def test_first_clean_sentence_falls_back_to_original_when_unsplittable():
    text = "no terminal punctuation at all in this text"
    assert first_clean_sentence_or_original(text) == text


# --- Integration tests through the real processor ---


@pytest.mark.asyncio
async def test_processor_reproduces_and_fixes_real_c3_shape():
    """Reproduces catalogue item C3 verbatim and confirms the validator
    resolves it to a single clean question rather than the concatenated
    wall of text."""
    guard = ResponseShapeValidatorProcessor()

    down_frames, _ = await run_test(guard, frames_to_send=_response(_C3_TEXT))

    text = _spoken_text(down_frames)
    assert text == "Which one sounds interesting?"
    assert count_questions(text) == 1


@pytest.mark.asyncio
async def test_processor_passes_through_clean_response_unmodified():
    guard = ResponseShapeValidatorProcessor()

    down_frames, _ = await run_test(
        guard, frames_to_send=_response("How many guests will be staying with you?")
    )

    text = _spoken_text(down_frames)
    assert text == "How many guests will be staying with you?"


@pytest.mark.asyncio
async def test_processor_no_false_positives_against_a_realistic_sample():
    """A validator that also mangles normal replies is worse than the
    problem it fixes -- confirm zero false positives against a sample of
    realistic, single-topic, complete turns."""
    guard = ResponseShapeValidatorProcessor()

    realistic_turns = [
        "Sure, the Cabana 1BHK sleeps two guests. Interested?",
        "That comes to ₹18,700 total for Ocean View Villa, all inclusive.",
        "Got it, Priya. What dates are you looking at for your stay?",
        "This one's a great fit: Ocean View Villa, a three-bedroom villa with pool and parking in Goa "
        "for ₹12,000 a night, sleeps 6.",
        "I've let the caretaker know, they'll take care of it shortly.",
        "Which property would you like to go ahead with?",
        "Great, I've noted that down for you.",
    ]
    for turn in realistic_turns:
        down_frames, _ = await run_test(guard, frames_to_send=_response(turn))
        text = _spoken_text(down_frames)
        assert text == turn, f"false positive mangled a clean turn: {turn!r} -> {text!r}"


@pytest.mark.asyncio
async def test_processor_multiple_greetings_gets_trimmed():
    guard = ResponseShapeValidatorProcessor()

    down_frames, _ = await run_test(
        guard, frames_to_send=_response("Hi there! Namaste, welcome to our property. How can I help?")
    )

    text = _spoken_text(down_frames)
    assert text == "Hi there!"


@pytest.mark.asyncio
async def test_processor_duplicated_punctuation_flood_gets_trimmed():
    guard = ResponseShapeValidatorProcessor()

    down_frames, _ = await run_test(
        guard, frames_to_send=_response("Sure, one moment.", " .. .. .. .. .. ..")
    )

    text = _spoken_text(down_frames)
    assert ".. .." not in text


@pytest.mark.asyncio
async def test_processor_never_leaves_the_guest_with_nothing():
    """Even a heavily-violating response resolves to SOME real content, not
    silence -- a validator that drops everything is worse than one that
    over-corrects."""
    guard = ResponseShapeValidatorProcessor()

    down_frames, _ = await run_test(guard, frames_to_send=_response(_C3_TEXT))

    text = _spoken_text(down_frames)
    assert text.strip() != ""
