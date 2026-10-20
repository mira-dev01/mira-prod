"""Covers LowConfidenceTranscriptGuardProcessor: a transcript Sarvam itself
flagged as low language-detection confidence must be substituted with a
clarification request before it reaches the LLM, never forwarded as-is.
"""

import pytest
from pipecat.frames.frames import TranscriptionFrame
from pipecat.tests.utils import run_test

from app.voice.low_confidence_transcript_guard import (
    DEFAULT_CLARIFICATION_TEXT,
    LowConfidenceTranscriptGuardProcessor,
)


def _transcription(text: str, language_probability: float | None) -> TranscriptionFrame:
    result = None
    if language_probability is not None:
        result = {"data": {"language_probability": language_probability}}
    return TranscriptionFrame(text=text, user_id="guest", timestamp="", result=result)


@pytest.mark.asyncio
async def test_low_confidence_transcript_is_replaced_with_clarification():
    processor = LowConfidenceTranscriptGuardProcessor(threshold=0.85)

    down_frames, _ = await run_test(
        processor,
        frames_to_send=[_transcription("के लिए लगा के हिसाब से", language_probability=0.843)],
    )

    transcriptions = [f for f in down_frames if isinstance(f, TranscriptionFrame)]
    assert len(transcriptions) == 1
    assert transcriptions[0].text == DEFAULT_CLARIFICATION_TEXT


@pytest.mark.asyncio
async def test_high_confidence_transcript_passes_through_unchanged():
    processor = LowConfidenceTranscriptGuardProcessor(threshold=0.85)

    down_frames, _ = await run_test(
        processor,
        frames_to_send=[_transcription("Delhi ke hisaab se batao", language_probability=0.95)],
    )

    transcriptions = [f for f in down_frames if isinstance(f, TranscriptionFrame)]
    assert len(transcriptions) == 1
    assert transcriptions[0].text == "Delhi ke hisaab se batao"


@pytest.mark.asyncio
async def test_missing_language_probability_is_a_no_op_not_an_error():
    """A fixed (non-"unknown") language_code, a different STT provider, or a
    missing field must never break the pipeline -- fail open, same discipline
    every other optional signal in this pipeline uses."""
    processor = LowConfidenceTranscriptGuardProcessor(threshold=0.85)

    down_frames, _ = await run_test(
        processor,
        frames_to_send=[_transcription("hello there", language_probability=None)],
    )

    transcriptions = [f for f in down_frames if isinstance(f, TranscriptionFrame)]
    assert len(transcriptions) == 1
    assert transcriptions[0].text == "hello there"


@pytest.mark.asyncio
async def test_blank_transcript_is_untouched():
    """Blank/whitespace-only transcripts are SilenceWatchdogProcessor's
    concern, not this guard's -- this processor must not touch them."""
    processor = LowConfidenceTranscriptGuardProcessor(threshold=0.85)

    down_frames, _ = await run_test(
        processor,
        frames_to_send=[_transcription("   ", language_probability=0.1)],
    )

    transcriptions = [f for f in down_frames if isinstance(f, TranscriptionFrame)]
    assert len(transcriptions) == 1
    assert transcriptions[0].text == "   "


@pytest.mark.asyncio
async def test_boundary_probability_exactly_at_threshold_passes_through():
    """threshold comparison is strictly less-than -- a probability exactly
    equal to the threshold is treated as confident enough."""
    processor = LowConfidenceTranscriptGuardProcessor(threshold=0.85)

    down_frames, _ = await run_test(
        processor,
        frames_to_send=[_transcription("borderline", language_probability=0.85)],
    )

    transcriptions = [f for f in down_frames if isinstance(f, TranscriptionFrame)]
    assert transcriptions[0].text == "borderline"


@pytest.mark.asyncio
async def test_default_threshold_no_longer_misfires_on_ordinary_hinglish():
    """Regression for the critical live bug: the 0.85 default used to fire
    on essentially every genuine Hindi/Hinglish utterance, since a
    code-mixed sentence is inherently ambiguous between hi-IN/en-IN and
    routinely scores well below 0.85 even when transcribed correctly. Uses
    the module DEFAULT (no threshold override) -- this is the actual
    production behavior, not just the mechanism in isolation."""
    processor = LowConfidenceTranscriptGuardProcessor()

    down_frames, _ = await run_test(
        processor,
        frames_to_send=[_transcription("Delhi ke hisaab se batao", language_probability=0.65)],
    )

    transcriptions = [f for f in down_frames if isinstance(f, TranscriptionFrame)]
    assert transcriptions[0].text == "Delhi ke hisaab se batao"


@pytest.mark.asyncio
async def test_default_threshold_still_catches_genuinely_low_confidence():
    """The recalibration trades away the original 0.843 edge case, but must
    still catch something -- a near-coin-flip/no-real-read score should
    still trigger clarification at the new default."""
    processor = LowConfidenceTranscriptGuardProcessor()

    down_frames, _ = await run_test(
        processor,
        frames_to_send=[_transcription("के लिए लगा के हिसाब से", language_probability=0.25)],
    )

    transcriptions = [f for f in down_frames if isinstance(f, TranscriptionFrame)]
    assert transcriptions[0].text == DEFAULT_CLARIFICATION_TEXT
