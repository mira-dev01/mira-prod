"""Covers Phase 4.3 (documentation/agent-conversation-improvement.md):
ResponseShapeValidatorProcessor -- a final structural gate right before TTS,
catching malformed/concatenated response shapes that RepetitionGuardProcessor's
word-overlap check structurally cannot see (different sentences glued
together have low word overlap by construction).
"""

import pytest
from pipecat.frames.frames import LLMFullResponseEndFrame, LLMFullResponseStartFrame, LLMTextFrame
from pipecat.tests.utils import run_test

from app.voice.conversation_quality import ConversationQuality
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
    cuts the response before the concatenated wall of text finishes.

    Streaming-first rewrite behavior, deliberately different from the old
    whole-buffer version's exact output: has_multiple_unconnected_questions
    mathematically cannot fire until 3 question-bearing segments exist in
    the accumulated text, so a genuinely streaming validator (which forwards
    each sentence the instant it completes, never holding the whole
    response back to look ahead) cannot detect this violation before
    sentence index 3 -- sentences 0-2 have already been forwarded (and, in
    a live call, already reached tts) by the time the guard has enough text
    to know a violation exists at all. This is the real, accepted tradeoff
    of true streaming for a check that inherently needs multi-sentence
    context, confirmed correct rather than a regression -- more real
    content reaches the guest than the old version ever forwarded, and the
    cut still happens as early as it is mathematically possible to detect."""
    guard = ResponseShapeValidatorProcessor()

    down_frames, _ = await run_test(guard, frames_to_send=_response(_C3_TEXT))

    text = _spoken_text(down_frames)
    assert text == (
        "Which one sounds interesting?Got it, Abhaya. "
        "Which of those two would you like to explore further?"
    )
    assert count_questions(text) == 2
    assert "We'll check availability" not in text
    assert "Which property would you like to go ahead with?" not in text


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


# --- Streaming behavior: the actual architectural requirement ---


@pytest.mark.asyncio
async def test_first_sentence_is_forwarded_before_the_response_ends():
    """The core streaming claim: a clean, completed sentence must reach
    downstream (tts) the moment it completes, not only once the whole
    response has finished generating -- proven by pushing frames directly
    (not via run_test's own harness) and inspecting what the processor has
    already forwarded BEFORE LLMFullResponseEndFrame is even sent."""
    forwarded: list[LLMTextFrame] = []

    class _CollectingProcessor(ResponseShapeValidatorProcessor):
        async def push_frame(self, frame, direction=None):
            if isinstance(frame, LLMTextFrame):
                forwarded.append(frame)

    guard = _CollectingProcessor()
    from pipecat.processors.frame_processor import FrameDirection

    await guard.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    await guard.process_frame(LLMTextFrame("First sentence done. "), FrameDirection.DOWNSTREAM)

    # The response has NOT ended yet -- confirm the completed sentence's
    # own CONTENT already went out, proving it wasn't held back until
    # teardown. Its trailing separator space is deliberately still pending
    # (not yet forwarded) at this point -- see process_frame's own comment
    # on _pending_separator: it's only released once proven real content
    # follows it, which hasn't happened yet.
    assert "".join(f.text for f in forwarded) == "First sentence done."

    await guard.process_frame(LLMTextFrame("Still talking"), FrameDirection.DOWNSTREAM)
    await guard.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    assert "".join(f.text for f in forwarded) == "First sentence done. Still talking"


@pytest.mark.asyncio
async def test_multi_chunk_streaming_reconstructs_text_exactly():
    """Real LLM output arrives as many small token-level chunks, not one
    whole string -- confirms the sentence-boundary accumulation logic
    handles arbitrary chunk boundaries (including mid-word splits) and
    still reconstructs the exact original text when nothing violates."""
    guard = ResponseShapeValidatorProcessor()
    chunks = ["Sure, the", " Cabana 1BHK", " sleeps two", " guests.", " Interested", "?"]

    down_frames, _ = await run_test(guard, frames_to_send=_response(*chunks))

    text = _spoken_text(down_frames)
    assert text == "Sure, the Cabana 1BHK sleeps two guests. Interested?"


# --- ConversationQuality integration ---


@pytest.mark.asyncio
async def test_records_validation_result_on_shape_violation():
    quality = ConversationQuality()
    guard = ResponseShapeValidatorProcessor(quality)

    await run_test(guard, frames_to_send=_response(_C3_TEXT))

    assert len(quality.validations) == 1
    result = quality.validations[0]
    assert result.rule == "shape_compliance"
    assert result.severity == "WARNING"
    assert "multiple_unconnected_questions" in result.metadata["violations"]
    assert result.processing_time_ms >= 0


@pytest.mark.asyncio
async def test_no_validation_result_recorded_for_a_clean_response():
    quality = ConversationQuality()
    guard = ResponseShapeValidatorProcessor(quality)

    await run_test(guard, frames_to_send=_response("How many guests will be staying with you?"))

    assert quality.validations == []


@pytest.mark.asyncio
async def test_processor_works_without_a_quality_object():
    """quality is optional -- every existing call site/test that constructs
    this processor without one (the vast majority in this file) must keep
    working unchanged, no-op rather than erroring."""
    guard = ResponseShapeValidatorProcessor()

    down_frames, _ = await run_test(guard, frames_to_send=_response(_C3_TEXT))

    assert _spoken_text(down_frames) != ""
