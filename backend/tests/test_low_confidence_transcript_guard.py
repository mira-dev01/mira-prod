"""The low-confidence transcript guard never rewrites the guest's words.

`classify` is the single copy of the rule (the audio telemetry observer calls it
too). Its table below uses the real values from test call 56947e99 (2026-11-13),
where four correct transcripts were replaced by an English apology written into the
guest's turn, and Mira asked the same question four times.
"""

import pytest
from pipecat.frames.frames import TranscriptionFrame, TTSUpdateSettingsFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.tests.utils import run_test
from pipecat.transcriptions.language import Language

from app.voice.conversation_state import ConversationState
from app.voice.conversation_style import ConversationStyleProcessor
from app.voice.language_sync import LanguageSyncProcessor
from app.voice.low_confidence_transcript_guard import (
    INAUDIBLE_MARKER,
    KnownEntities,
    LowConfidenceTranscriptGuardProcessor,
    classify,
)

ENTITIES = KnownEntities.from_names(["Bengaluru", "Mumbai", "Jugglers Rest Spacez Villa"])
TWELVE_WORD_HINDI = "मुझे अगले हफ्ते के लिए एक अच्छा विला चाहिए जिसमें पूल हो"


@pytest.mark.parametrize(
    ("text", "language", "score", "expected"),
    [
        ("Juhu Bangalore", "en-IN", 0.098, "pass"),
        ("Indira Nagar", "en-IN", 0.324, "pass"),
        ("Koramangala", "pa-IN", 0.177, "pass"),
        ("Wow", "en-IN", 0.221, "pass"),
        ("ਹੋਰ ਮੰਗਲਾ ਹੋਰ ਮੰਗਲਾ", "pa-IN", 0.611, "inaudible"),
        ("हाय मुझे 20th November का booking करना था।", "hi-IN", 0.783, "pass"),
        (TWELVE_WORD_HINDI, "hi-IN", 0.20, "pass_flagged"),
    ],
)
def test_real_values_from_the_test_call(text, language, score, expected):
    assert classify(text, score, language, ENTITIES) == expected


def test_twelve_word_sentence_really_has_twelve_words():
    assert len(TWELVE_WORD_HINDI.split()) == 12


def test_known_entity_inside_a_long_low_score_sentence_passes():
    sentence = "mujhe agle hafte ke liye Bengaluru mein ek achha sa villa chahiye"
    assert classify(sentence, 0.15, "hi-IN", ENTITIES) == "pass"
    assert classify(sentence, 0.15, "hi-IN", KnownEntities()) == "pass_flagged"


def test_known_entity_matches_a_respelling_and_a_split_spelling():
    entities = KnownEntities.from_names(["Indiranagar"])
    long_sentence = "mujhe agle hafte ke liye {} mein ek achha sa villa chahiye"
    for spelling in ("Indira Nagar", "indiranagar", "Indra Nagar"):
        assert classify(long_sentence.format(spelling), 0.1, "hi-IN", entities) == "pass", spelling


def test_a_spoken_phone_number_passes_at_any_score():
    assert classify("nine eight seven six five four three two one zero", 0.05, "en-IN") == "pass"
    assert classify("९८७६५ ४३२१० ९८ ७६", 0.05, "hi-IN") == "pass"


def test_boundary_probability_exactly_at_threshold_passes():
    assert classify(TWELVE_WORD_HINDI, 0.4, "hi-IN") == "pass"
    assert classify(TWELVE_WORD_HINDI, 0.3999, "hi-IN") == "pass_flagged"


def test_missing_score_falls_back_to_text_rules_only():
    assert classify(TWELVE_WORD_HINDI, None, None) == "pass"
    # A script Mira does not speak is judged on the text, whatever the score.
    assert classify("ਹੋਰ ਮੰਗਲਾ ਹੋਰ ਮੰਗਲਾ", None, None) == "inaudible"


def _transcription(text: str, probability: float | None, language: Language | None = None) -> TranscriptionFrame:
    result = {"data": {"language_probability": probability, "language_code": "hi-IN"}} if probability is not None else None
    return TranscriptionFrame(text=text, user_id="guest", timestamp="", language=language, result=result)


async def _guard_output(frame: TranscriptionFrame, state: ConversationState | None = None) -> str:
    processor = LowConfidenceTranscriptGuardProcessor(known_entities=ENTITIES, conversation_state=state)
    down, _ = await run_test(processor, frames_to_send=[frame])
    return [f for f in down if isinstance(f, TranscriptionFrame)][0].text


@pytest.mark.asyncio
async def test_a_place_name_with_a_terrible_score_reaches_the_model_unchanged():
    assert await _guard_output(_transcription("Koramangala", 0.177)) == "Koramangala"


@pytest.mark.asyncio
async def test_wrong_script_becomes_the_marker_not_a_sentence():
    assert await _guard_output(_transcription("ਹੋਰ ਮੰਗਲਾ ਹੋਰ ਮੰਗਲਾ", 0.611)) == INAUDIBLE_MARKER


@pytest.mark.asyncio
async def test_flagged_transcript_keeps_its_text_and_sets_a_one_turn_flag():
    state = ConversationState()
    assert await _guard_output(_transcription(TWELVE_WORD_HINDI, 0.2), state) == TWELVE_WORD_HINDI
    assert state.last_transcript_low_confidence is True

    # The next confident transcript clears it: the hint lasts one turn.
    assert await _guard_output(_transcription("Delhi ke hisaab se batao", 0.9), state) == "Delhi ke hisaab se batao"
    assert state.last_transcript_low_confidence is False


@pytest.mark.asyncio
async def test_missing_language_probability_is_a_no_op_not_an_error():
    assert await _guard_output(_transcription("hello there", None)) == "hello there"


@pytest.mark.asyncio
async def test_blank_transcript_is_untouched():
    """Blank/whitespace-only transcripts are SilenceWatchdogProcessor's concern."""
    assert await _guard_output(_transcription("   ", 0.1)) == "   "


@pytest.mark.asyncio
async def test_inaudible_marker_does_not_move_tts_language_or_style():
    state = ConversationState()
    pipeline = Pipeline(
        [LowConfidenceTranscriptGuardProcessor(conversation_state=state), LanguageSyncProcessor(state), ConversationStyleProcessor(state)]
    )

    down, _ = await run_test(
        pipeline,
        frames_to_send=[
            _transcription("हाँ मुझे बुकिंग करनी है", 0.9, Language.HI_IN),
        ],
    )
    style_before, language_before = state.conversation_style, state.current_spoken_language
    switches_before = sum(isinstance(f, TTSUpdateSettingsFrame) for f in down)
    assert style_before is not None and switches_before == 1

    down, _ = await run_test(
        pipeline,
        frames_to_send=[_transcription("ਹੋਰ ਮੰਗਲਾ ਹੋਰ ਮੰਗਲਾ", 0.611, Language.EN_IN)],
    )

    assert [f.text for f in down if isinstance(f, TranscriptionFrame)] == [INAUDIBLE_MARKER]
    assert not any(isinstance(f, TTSUpdateSettingsFrame) for f in down)  # no switch to English
    assert state.current_spoken_language == language_before
    assert state.conversation_style == style_before


# --- the names loaded for a call (app/voice/pipeline.py) ---


@pytest.mark.asyncio
async def test_known_entities_for_a_host_cover_every_city_and_property_name(db_session, test_user):
    from app.models.property import Property
    from app.voice.pipeline import _load_known_entities

    db_session.add_all(
        [
            Property(
                user_id=test_user.id, name="Pine Cottage", city="Bengaluru", locality="Koramangala", base_price=3000, max_guests=2,
                exophone="+918011130001",
            ),
            Property(user_id=test_user.id, name="Sea View", city="Mumbai", base_price=3000, max_guests=2, exophone="+918011130002"),
        ]
    )
    await db_session.commit()

    entities = await _load_known_entities(test_user.id, None)

    long_low_score = "mujhe agle hafte ke liye {} mein ek achha sa villa chahiye"
    for name in ("Bengaluru", "Mumbai", "Pine Cottage", "Sea View", "Koramangala"):
        assert classify(long_low_score.format(name), 0.1, "hi-IN", entities) == "pass", name
    assert classify(long_low_score.format("Jaipur"), 0.1, "hi-IN", entities) == "pass_flagged"


@pytest.mark.asyncio
async def test_known_entities_for_a_single_property_call_cover_only_that_property(db_session, test_user):
    from app.models.property import Property
    from app.voice.pipeline import _load_known_entities

    pine = Property(user_id=test_user.id, name="Pine Cottage", city="Bengaluru", base_price=3000, max_guests=2, exophone="+918011130003")
    sea = Property(user_id=test_user.id, name="Sea View", city="Mumbai", base_price=3000, max_guests=2, exophone="+918011130004")
    db_session.add_all([pine, sea])
    await db_session.commit()

    entities = await _load_known_entities(test_user.id, pine.id)

    sentence = "mujhe agle hafte ke liye {} mein ek achha sa villa chahiye"
    assert classify(sentence.format("Bengaluru"), 0.1, "hi-IN", entities) == "pass"
    assert classify(sentence.format("Mumbai"), 0.1, "hi-IN", entities) == "pass_flagged"


@pytest.mark.asyncio
async def test_known_entities_fail_open_when_the_lookup_breaks(monkeypatch):
    from app.voice import pipeline

    class _Broken:
        async def __aenter__(self):
            raise RuntimeError("database unavailable")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(pipeline, "AsyncSessionLocal", lambda: _Broken())

    entities = await pipeline._load_known_entities(__import__("uuid").uuid4(), None)

    assert entities == KnownEntities()
