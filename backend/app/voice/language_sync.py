"""Keeps Sarvam TTS synthesizing in whatever language the guest is actually
speaking.

Sarvam STT (mode="codemix", app/voice/pipeline.py) already auto-detects the
spoken language per utterance and attaches it to each TranscriptionFrame --
see pipecat.services.sarvam.stt's _map_language_code_to_enum. Sarvam TTS,
however, is never told about it and is left at the SDK's hardcoded "en-IN"
default, so a Hindi (or Hinglish, which Sarvam tags as Hindi) reply from the
LLM would previously be synthesized with English phonemes/prosody.

LanguageSyncProcessor sits between stt and the context aggregator. On every
TranscriptionFrame it compares the detected language against the last one it
applied and, only on a change, pushes a TTSUpdateSettingsFrame downstream.
Sarvam TTS's own _update_settings picks that frame up (it's a delta with only
`language` set, so voice/model/pace are left completely untouched) and
reconfigures its live websocket via _send_config -- no reconnect, no dropped
audio. TTSUpdateSettingsFrame is a ControlFrame, so every other processor in
the pipeline (user_aggregator, the LLM service) forwards it downstream
unchanged since none of them match on that frame type.

Phase 3.1 (documentation/agent-conversation-improvement.md): this processor
also writes the detected language to ConversationState.current_spoken_language,
the same signal that already drives the TTS switch above, now also fed back
so the LLM's own reply-language choice (Phase 3.2, via StatePromptSyncProcessor)
can be told directly what language the guest is currently speaking, instead of
that being discarded after the TTS switch as it was before this task.
"""

from loguru import logger

from pipecat.frames.frames import Frame, TranscriptionFrame, TTSUpdateSettingsFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.transcriptions.language import Language

from app.voice.conversation_state import ConversationState

# Sarvam STT tags codemixed Hindi/English speech (and anything it can't
# confidently place) as Hindi -- see _map_language_code_to_enum's fallback in
# pipecat.services.sarvam.stt. Anything in this set should get a Hindi TTS
# voice; everything else (currently just English) keeps the configured
# default.
_HINDI_LANGUAGES = {Language.HI, Language.HI_IN}

DEFAULT_TTS_LANGUAGE = Language.EN_IN
HINDI_TTS_LANGUAGE = Language.HI_IN


class LanguageSyncProcessor(FrameProcessor):
    """Mirrors the guest's detected speech language onto the TTS service."""

    def __init__(self, conversation_state: ConversationState | None = None):
        super().__init__()
        # Matches the TTS's own initial settings (app/voice/pipeline.py) so
        # the first English utterance doesn't trigger a redundant no-op
        # TTSUpdateSettingsFrame / websocket reconfigure.
        self._current_tts_language: Language | None = DEFAULT_TTS_LANGUAGE
        # Optional so every existing call site/test that constructs this
        # processor without a ConversationState (there wasn't one to pass
        # before Phase 1) keeps working unchanged -- state-writing is a
        # no-op, not an error, when this is None.
        self._conversation_state = conversation_state

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.language is not None:
            if self._conversation_state is not None:
                self._conversation_state.current_spoken_language = frame.language

            target = HINDI_TTS_LANGUAGE if frame.language in _HINDI_LANGUAGES else DEFAULT_TTS_LANGUAGE
            if target != self._current_tts_language:
                logger.debug(
                    "Guest speech detected as {}; switching TTS language to {}",
                    frame.language,
                    target,
                )
                self._current_tts_language = target
                await self.push_frame(
                    TTSUpdateSettingsFrame(delta=SarvamTTSService.Settings(language=target)),
                    direction,
                )

        await self.push_frame(frame, direction)
