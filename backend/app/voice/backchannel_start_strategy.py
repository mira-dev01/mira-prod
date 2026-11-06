"""Turn-start strategy that lets the guest talk *over* Mira without every
"hmm" / "haan" / "okay" cutting her off.

Pipecat's default start strategies (VAD + transcription) broadcast an
interruption the instant VAD hears the guest, which on a phone line means any
acknowledgement ("achha", "hmm", a cough) cancels Mira's in-progress TTS and
forces a TTS reconnect. That is the "walkie-talkie" behavior: the line is
effectively half-duplex because every sound the guest makes is a floor grab.

This strategy keeps the exact same behavior while Mira is silent (a VAD start
opens the guest's turn immediately), but while Mira is *speaking* it splits
"guest made a sound" from "guest wants the floor":

- A VAD burst only interrupts once it has lasted `min_interrupt_secs`.
  Acknowledgements are short; a real interruption ("wait, we're ten people")
  keeps going. No STT needed, so a genuine barge-in is not held back by
  Sarvam's lack of interim transcripts.
- A transcript that arrives while Mira is speaking interrupts only if it is
  not a pure acknowledgement. Pure acknowledgements are dropped from the
  aggregation so they never become an LLM turn.
- The same drop applies to the acknowledgement's transcript when it lands just
  after Mira stopped (STT is slower than the audio), so "hmm" never makes Mira
  answer "hmm".

Nothing here calls an LLM or changes what Mira says; it only decides whether
the guest's sound opens a turn. Gated by settings.backchannel_filter_enabled.
"""

import asyncio
import re
import time

from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.base_user_turn_start_strategy import BaseUserTurnStartStrategy

# Acknowledgement tokens in the languages guests actually mix (English,
# romanized Hinglish, and Devanagari -- Sarvam codemix returns Hindi in
# Devanagari). A transcript made up ONLY of these, and short, is treated as a
# backchannel. Deliberately excludes anything that carries information
# ("no", numbers, names, "wait", "stop", "nahi", "ruko") -- those must always
# be able to interrupt.
_BACKCHANNEL_WORDS = {
    "ok", "okay", "k", "hmm", "hm", "mm", "mmm", "mhm", "uh", "huh", "oh", "ah", "aah", "um", "umm",
    "yes", "yeah", "yep", "yup", "right", "sure", "fine", "good", "great", "i", "see", "got", "it",
    "haan", "han", "ha", "ji", "achha", "accha", "acha", "theek", "thik", "hai", "sahi", "bilkul",
    "हाँ", "हां", "हा", "जी", "अच्छा", "ठीक", "है", "सही", "बिलकुल", "हम्म", "हम", "ओके", "ओह",
}

_MAX_BACKCHANNEL_WORDS = 3
# How long after a suppressed VAD burst a lone acknowledgement transcript is
# still treated as that burst's text (STT lags the audio by ~1-2s).
_SUPPRESSED_BURST_TRANSCRIPT_WINDOW_SECS = 4.0

_WORD_RE = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)


def is_backchannel(text: str) -> bool:
    """True when `text` is only a short acknowledgement ("hmm", "ok ji")."""
    words = [w.lower() for w in _WORD_RE.findall(text or "")]
    if not words or len(words) > _MAX_BACKCHANNEL_WORDS:
        return False
    return all(w in _BACKCHANNEL_WORDS for w in words)


class BackchannelAwareUserTurnStartStrategy(BaseUserTurnStartStrategy):
    def __init__(self, *, min_interrupt_secs: float = 0.5, **kwargs):
        super().__init__(**kwargs)
        self._min_interrupt_secs = min_interrupt_secs
        self._bot_speaking = False
        self._vad_speaking = False
        self._interrupt_task: asyncio.Task | None = None
        self._suppressed_burst_at: float | None = None

    async def reset(self):
        await super().reset()
        await self._cancel_interrupt_timer()

    async def cleanup(self):
        await super().cleanup()
        await self._cancel_interrupt_timer()

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            await self._cancel_interrupt_timer()
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            return await self._handle_vad_started()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            await self._handle_vad_stopped()
        elif isinstance(frame, InterimTranscriptionFrame):
            return await self._handle_interim(frame)
        elif isinstance(frame, TranscriptionFrame):
            return await self._handle_transcription(frame)
        return ProcessFrameResult.CONTINUE

    async def _handle_interim(self, frame: InterimTranscriptionFrame) -> ProcessFrameResult:
        """Partial text (only if the STT provides it -- Sarvam does not today).

        Lets a genuine interruption fire the moment real words are heard, well
        before the 0.5s sustained-speech timer, and never reacts to a partial
        that is still just an acknowledgement.
        """
        if self._bot_speaking and frame.text.strip() and not is_backchannel(frame.text):
            await self._cancel_interrupt_timer()
            logger.info("backchannel_filter_barge_in_from_partial text=%r", frame.text)
            await self.trigger_user_turn_started()
            return ProcessFrameResult.STOP
        return ProcessFrameResult.CONTINUE

    async def _handle_vad_started(self) -> ProcessFrameResult:
        self._vad_speaking = True
        if not self._bot_speaking:
            await self.trigger_user_turn_started()
            return ProcessFrameResult.STOP
        # Mira is talking: don't grab the floor yet, see if this is sustained.
        await self._cancel_interrupt_timer()
        self._interrupt_task = self.task_manager.create_task(
            self._interrupt_after_sustained_speech(), f"{self}::_interrupt_after_sustained_speech"
        )
        return ProcessFrameResult.CONTINUE

    async def _handle_vad_stopped(self):
        self._vad_speaking = False
        if self._interrupt_task is not None:
            # Burst ended before the threshold: an acknowledgement, not an interruption.
            await self._cancel_interrupt_timer()
            self._suppressed_burst_at = time.monotonic()
            logger.info("backchannel_burst_suppressed threshold_secs=%.2f", self._min_interrupt_secs)

    async def _interrupt_after_sustained_speech(self):
        await asyncio.sleep(self._min_interrupt_secs)
        self._interrupt_task = None
        if self._vad_speaking and self._bot_speaking:
            logger.info("backchannel_filter_barge_in_confirmed threshold_secs=%.2f", self._min_interrupt_secs)
            await self.trigger_user_turn_started()

    async def _handle_transcription(self, frame: TranscriptionFrame) -> ProcessFrameResult:
        recently_suppressed = (
            self._suppressed_burst_at is not None
            and time.monotonic() - self._suppressed_burst_at <= _SUPPRESSED_BURST_TRANSCRIPT_WINDOW_SECS
        )
        if (self._bot_speaking or recently_suppressed) and is_backchannel(frame.text):
            logger.info("backchannel_transcript_dropped text=%r", frame.text)
            await self.trigger_reset_aggregation()
            return ProcessFrameResult.STOP
        # Fallback for a guest VAD missed (very soft speech) -- same as the
        # default TranscriptionUserTurnStartStrategy; also the path by which a
        # substantive mid-bot-speech transcript interrupts.
        self._suppressed_burst_at = None
        await self.trigger_user_turn_started()
        return ProcessFrameResult.STOP

    async def _cancel_interrupt_timer(self):
        task, self._interrupt_task = self._interrupt_task, None
        if task is not None:
            await self.task_manager.cancel_task(task)
