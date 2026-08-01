"""Covers Phase 3.1 (documentation/agent-conversation-improvement.md):
LanguageSyncProcessor writes the guest's detected spoken language to
ConversationState.current_spoken_language, the same signal that already
drives the TTS-voice switch (pre-existing, unchanged behavior -- covered
here too since no test file existed for this processor before this task).
"""

import pytest
from pipecat.frames.frames import TranscriptionFrame, TTSUpdateSettingsFrame
from pipecat.tests.utils import run_test
from pipecat.transcriptions.language import Language

from app.voice.conversation_state import ConversationState
from app.voice.language_sync import DEFAULT_TTS_LANGUAGE, HINDI_TTS_LANGUAGE, LanguageSyncProcessor


def _transcription(language: Language) -> TranscriptionFrame:
    return TranscriptionFrame(text="hello", user_id="guest", timestamp="", language=language)


@pytest.mark.asyncio
async def test_writes_detected_language_to_conversation_state():
    state = ConversationState()
    processor = LanguageSyncProcessor(state)

    await run_test(processor, frames_to_send=[_transcription(Language.HI)])

    assert state.current_spoken_language == Language.HI


@pytest.mark.asyncio
async def test_updates_conversation_state_on_language_switch():
    state = ConversationState()
    processor = LanguageSyncProcessor(state)

    await run_test(processor, frames_to_send=[_transcription(Language.EN)])
    assert state.current_spoken_language == Language.EN

    await run_test(processor, frames_to_send=[_transcription(Language.HI_IN)])
    assert state.current_spoken_language == Language.HI_IN


@pytest.mark.asyncio
async def test_no_conversation_state_is_a_no_op_not_an_error():
    """Every existing call site/test that constructs this processor without
    a ConversationState (there wasn't one to pass before Phase 1) must keep
    working unchanged."""
    processor = LanguageSyncProcessor()
    down_frames, _ = await run_test(processor, frames_to_send=[_transcription(Language.HI)])
    # Still forwards the transcription frame and still switches TTS --
    # state-writing being skipped doesn't break the pre-existing behavior.
    assert any(isinstance(f, TTSUpdateSettingsFrame) for f in down_frames)


@pytest.mark.asyncio
async def test_tts_switch_behavior_unchanged_by_state_wiring():
    """Regression: confirms Phase 3.1's addition didn't change the existing,
    already-correct TTS-language-switch behavior this processor's docstring
    describes."""
    state = ConversationState()
    processor = LanguageSyncProcessor(state)

    down_frames, _ = await run_test(processor, frames_to_send=[_transcription(Language.HI)])
    settings_frames = [f for f in down_frames if isinstance(f, TTSUpdateSettingsFrame)]
    assert len(settings_frames) == 1
    assert settings_frames[0].delta.language == HINDI_TTS_LANGUAGE


@pytest.mark.asyncio
async def test_no_redundant_tts_switch_for_same_language_twice():
    state = ConversationState()
    processor = LanguageSyncProcessor(state)

    down_frames, _ = await run_test(
        processor, frames_to_send=[_transcription(Language.HI), _transcription(Language.HI)]
    )
    settings_frames = [f for f in down_frames if isinstance(f, TTSUpdateSettingsFrame)]
    # Both TranscriptionFrames still update conversation_state (harmless,
    # idempotent), but only the first actually changes the TTS language.
    assert len(settings_frames) == 1
    assert state.current_spoken_language == Language.HI
