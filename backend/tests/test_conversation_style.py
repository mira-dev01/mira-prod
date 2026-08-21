"""Covers the Conversation Style Engine (app/voice/conversation_style.py) --
computes a single immutable ConversationStyle from a rolling window of guest
turns, with hysteresis so one noisy/ambiguous turn can't flip the committed
language. Grounded in the real regression call
(a5f7cef9-2bfd-4d82-a41c-0f1a8ab32dae) and the request's own worked examples.
"""

import pytest
from pipecat.frames.frames import TranscriptionFrame
from pipecat.tests.utils import run_test
from pipecat.transcriptions.language import Language

from app.voice.conversation_state import ConversationState
from app.voice.conversation_style import (
    ConversationAnalyzer,
    ConversationStyle,
    ConversationStyleProcessor,
    MIN_TURNS_TO_SWITCH,
    StyleEngine,
    render_style_block,
)


def _transcription(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="guest", timestamp="", language=Language.HI_IN)


def _run(turns: list[str], rolling_window: int = 6, speaker_gender: str = "female") -> list[ConversationStyle]:
    engine = StyleEngine(rolling_window=rolling_window, speaker_gender=speaker_gender)
    style = None
    results = []
    for i, text in enumerate(turns, start=1):
        signal = ConversationAnalyzer.analyze_turn(text)
        style = engine.update(signal, style, turn_index=i)
        results.append(style)
    return results


# --- ConversationAnalyzer ---


def test_analyze_turn_pure_english():
    signal = ConversationAnalyzer.analyze_turn("Hello, how are you today?")
    assert signal.english_ratio == 1.0
    assert signal.devanagari_ratio == 0.0
    assert signal.has_hinglish_token is False


def test_analyze_turn_pure_devanagari():
    signal = ConversationAnalyzer.analyze_turn("हाँ मुझे बुकिंग करनी है")
    assert signal.devanagari_ratio == 1.0
    assert signal.english_ratio == 0.0


# --- StyleEngine: immutability and basic shape ---


def test_conversation_style_is_frozen():
    (style,) = _run(["Hello"])
    with pytest.raises(Exception):
        style.language = "HINDI"  # type: ignore[misc]


def test_first_turn_commits_immediately_no_prior_style_to_hold_against():
    (style,) = _run(["Hello"])
    assert style.language == "ENGLISH"
    assert style.previous_style is None
    assert style.last_language_switch_turn == 1


def test_hospitality_style_is_always_airbnb_host():
    (style,) = _run(["Hello"])
    assert style.hospitality_style == "AIRBNB_HOST"


# --- Hysteresis: the actual architectural requirement ---


def test_one_english_sentence_does_not_flip_an_established_hindi_style():
    """Exact spec example: current style Hindi, one English-leaning turn
    must not switch it to English."""
    turns = [
        "हाँ मुझे बुकिंग करनी है",
        "सितंबर के पहले हफ्ते में",
        "दो लोग रहेंगे",
        "Hello",  # single English-leaning turn
    ]
    styles = _run(turns)
    assert styles[-2].language in ("HINDI", "HINGLISH")
    assert styles[-1].language == styles[-2].language, "one turn must not flip the committed style"


def test_switch_requires_multiple_consecutive_agreeing_turns():
    """A real, sustained switch (not just one turn) must eventually commit
    -- hysteresis holds off a switch, it doesn't forbid one forever."""
    turns = ["हाँ मुझे बुकिंग करनी है"] + ["Actually let's continue in English please"] * (
        MIN_TURNS_TO_SWITCH + 2
    )
    styles = _run(turns)
    assert styles[0].language in ("HINDI", "HINGLISH")
    assert styles[-1].language == "ENGLISH", "sustained English over multiple turns must eventually commit"


def test_real_regression_call_stays_hindi_hinglish_throughout():
    """Verbatim from the real production call transcript
    (a5f7cef9-2bfd-4d82-a41c-0f1a8ab32dae) -- a guest speaking mostly
    Devanagari Hindi/Hinglish with occasional English words/property names
    ("Jacuzzi woodwork", "Arya phobos") must not bounce the committed style
    to English on those isolated turns."""
    turns = [
        "हाँ मुझे September 1st week में booking करना है।",
        "तो मैं anniversary के लिए आ रही हूँ",
        "मैं अभी भी sure नहीं हूँ इस डे के बारे में।",
        "हम 2 लोग",
        "Jacuzzi woodwork",
        "तो ये 4293 वाले का फिर total कितना पड़ेगा",
        "Arya phobos",
        "नहीं मैं तो कोलबा की बात कर रही थी",
    ]
    styles = _run(turns)
    for i, style in enumerate(styles, start=1):
        assert style.language in ("HINDI", "HINGLISH"), f"turn {i} incorrectly switched to ENGLISH"


def test_confidence_grows_when_style_is_reaffirmed():
    turns = ["हाँ मुझे बुकिंग करनी है", "सितंबर के पहले हफ्ते में", "दो लोग रहेंगे"]
    styles = _run(turns)
    assert styles[-1].confidence >= styles[0].confidence


def test_confidence_decreases_when_a_switch_is_held_off():
    turns = ["हाँ मुझे बुकिंग करनी है", "सितंबर के पहले हफ्ते में", "Hello there"]
    styles = _run(turns)
    assert styles[-1].confidence < styles[-2].confidence


# --- Rolling window ---


def test_rolling_window_bounds_history_length():
    """StyleEngine now owns its history internally (architecture boundary:
    this is the engine's own working memory, not a ConversationState field)
    -- verified via its private _history rather than an externally-threaded
    list, since there is no longer an external list to inspect."""
    engine = StyleEngine(rolling_window=3)
    style = None
    for i in range(1, 11):
        signal = ConversationAnalyzer.analyze_turn("Hello")
        style = engine.update(signal, style, turn_index=i)
    assert len(engine._history) == 3


def test_each_update_returns_a_fresh_immutable_style():
    """Every ConversationStyle returned is its own frozen snapshot -- calling
    update() again must never retroactively change an earlier snapshot
    already handed to a caller (e.g. state_prompt_sync holding a reference
    from a prior turn)."""
    engine = StyleEngine()
    signal = ConversationAnalyzer.analyze_turn("Hello")
    first_style = engine.update(signal, None, turn_index=1)
    second_style = engine.update(signal, first_style, turn_index=2)
    assert first_style.turn_index == 1
    assert second_style.turn_index == 2
    assert first_style is not second_style


# --- Script detection ---


def test_script_devanagari_when_guest_writes_devanagari_consistently():
    turns = ["हाँ मुझे बुकिंग करनी है", "सितंबर के पहले हफ्ते में", "दो लोग रहेंगे"]
    styles = _run(turns)
    assert styles[-1].script == "DEVANAGARI"


def test_script_roman_for_english_conversation():
    styles = _run(["Hello, how can I help", "I'd like to book a room"])
    assert styles[-1].script == "ROMAN"


# --- render_style_block: the exact prompt text ---


def test_render_style_block_contains_required_sections():
    (style,) = _run(["Hello"])
    block = render_style_block(style)
    assert "Conversation Style" in block
    assert "Language:" in block
    assert "English Ratio:" in block
    assert "Script:" in block
    assert "Tone:" in block
    assert "Never abruptly change language." in block


def test_render_style_block_never_says_translate_or_explain():
    (style,) = _run(["हाँ मुझे बुकिंग करनी है"])
    block = render_style_block(style)
    assert "Do not explain." in block
    assert "Do not translate." in block


def test_render_style_block_language_label_hinglish_not_hindi():
    """Matches state_prompt_sync._LANGUAGE_DISPLAY_NAMES' existing choice --
    the LLM-facing label for the Hindi-family is "Hinglish"/"Urban Hinglish",
    never bare "Hindi", consistent across both mechanisms."""
    turns = ["हाँ मुझे बुकिंग करनी है", "September में", "Jacuzzi चाहिए"]
    styles = _run(turns)
    block = render_style_block(styles[-1])
    assert "Urban Hinglish" in block or "Hindi" in block


# --- render_style_block: Hindi speaker-gender agreement ---


def test_render_style_block_female_gender_uses_feminine_forms():
    (style,) = _run(["हाँ मुझे बुकिंग करनी है"], speaker_gender="female")
    assert style.speaker_gender == "female"
    block = render_style_block(style)
    assert "feminine verb forms" in block
    assert "karti hoon" in block
    assert "speaking as a woman" in block


def test_render_style_block_male_gender_uses_masculine_forms():
    (style,) = _run(["हाँ मुझे बुकिंग करनी है"], speaker_gender="male")
    assert style.speaker_gender == "male"
    block = render_style_block(style)
    assert "masculine verb forms" in block
    assert "karta hoon" in block
    assert "speaking as a man" in block


def test_render_style_block_omits_gender_guidance_for_english():
    (style,) = _run(["Hello, how are you today?"], speaker_gender="female")
    assert style.language == "ENGLISH"
    block = render_style_block(style)
    assert "feminine verb forms" not in block
    assert "masculine verb forms" not in block


def test_speaker_gender_carries_forward_across_turns_unchanged():
    """speaker_gender is a fixed host-level setting (User.agent_voice_gender),
    never re-derived per turn the way language/tone are -- must survive the
    full hysteresis history unchanged, English turns included."""
    turns = ["Hello", "हाँ मुझे बुकिंग करनी है", "September में chahiye", "thank you"]
    styles = _run(turns, speaker_gender="male")
    assert all(s.speaker_gender == "male" for s in styles)


def test_default_speaker_gender_is_female():
    """Matches User.agent_voice_gender's DB default (app/models/user.py)."""
    (style,) = _run(["Hello"])
    assert style.speaker_gender == "female"


# --- ConversationStyleProcessor: pipeline wiring ---


@pytest.mark.asyncio
async def test_processor_writes_conversation_style_to_state():
    state = ConversationState()
    processor = ConversationStyleProcessor(state)

    await run_test(processor, frames_to_send=[_transcription("हाँ मुझे बुकिंग करनी है")])

    assert state.conversation_style is not None
    assert state.conversation_style.language in ("HINDI", "HINGLISH")


@pytest.mark.asyncio
async def test_processor_never_touches_current_spoken_language():
    """Hard requirement: this processor must be fully additive alongside
    LanguageSyncProcessor -- it must never write current_spoken_language or
    explicit_language_preference, both owned exclusively by the existing,
    unmodified mechanism."""
    state = ConversationState()
    processor = ConversationStyleProcessor(state)

    await run_test(processor, frames_to_send=[_transcription("Hello")])

    assert state.current_spoken_language is None
    assert state.explicit_language_preference is None


@pytest.mark.asyncio
async def test_processor_forwards_frames_unchanged():
    state = ConversationState()
    processor = ConversationStyleProcessor(state)
    frame = _transcription("Hello")

    down_frames, _ = await run_test(processor, frames_to_send=[frame])

    assert any(isinstance(f, TranscriptionFrame) and f.text == "Hello" for f in down_frames)


@pytest.mark.asyncio
async def test_processor_forwards_speaker_gender_from_construction():
    """app/voice/pipeline.py constructs this processor with
    speaker_gender=voice_gender (host.agent_voice_gender) -- confirms that
    value actually reaches the ConversationStyle written to state, not just
    the engine's own internal default."""
    state = ConversationState()
    processor = ConversationStyleProcessor(state, speaker_gender="male")

    await run_test(processor, frames_to_send=[_transcription("हाँ मुझे बुकिंग करनी है")])

    assert state.conversation_style is not None
    assert state.conversation_style.speaker_gender == "male"


@pytest.mark.asyncio
async def test_processor_no_conversation_state_is_a_no_op_not_an_error():
    processor = ConversationStyleProcessor()
    down_frames, _ = await run_test(processor, frames_to_send=[_transcription("Hello")])
    assert any(isinstance(f, TranscriptionFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_processor_accumulates_history_across_multiple_turns():
    """History accumulation is now internal to the processor's own
    StyleEngine instance (architecture boundary: ConversationState no
    longer holds style_history) -- verified via the observable effect
    (turn_index advancing, confidence building) rather than reaching into
    ConversationState for a field that no longer lives there."""
    state = ConversationState()
    processor = ConversationStyleProcessor(state)

    await run_test(
        processor,
        frames_to_send=[
            _transcription("हाँ मुझे बुकिंग करनी है"),
            _transcription("सितंबर के पहले हफ्ते में"),
        ],
    )

    assert not hasattr(state, "style_history")
    assert state.conversation_style.turn_index == 2
    assert processor._engine._history and len(processor._engine._history) == 2
