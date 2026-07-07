"""Experimental turn-detection strategy (shagun branch only -- see
app/config.py's turn_detection_strategy comment; not wired into main/prod).

HybridCompletenessUserTurnStopStrategy keeps the same VAD-driven approach as
the production SpeechTimeoutUserTurnStopStrategy (app/voice/pipeline.py), but
instead of firing at a single fixed timeout, it runs a fast, non-LLM
completeness check on the transcript accumulated so far when that timeout
elapses. If the check thinks the guest is mid-sentence, it extends the wait
once more (up to a hard cap) instead of cutting them off. The goal is fewer
premature interruptions on trailing conjunctions/short pauses, without a
network round-trip that would add real latency.

Modeled closely on pipecat.turns.user_stop.SpeechTimeoutUserTurnStopStrategy
(same timer/state-machine shape) -- diverges only in what happens when the
base timeout expires.
"""

import asyncio
import random
import time

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    STTMetadataFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_stop.base_user_turn_stop_strategy import BaseUserTurnStopStrategy

# Trailing words that suggest the sentence isn't over yet, in the languages
# guests actually mix mid-call (see system_prompt.py's Hinglish rule).
# Sarvam STT (mode="codemix", app/voice/pipeline.py) transcribes Hindi in
# Devanagari script, not romanized -- both spellings are listed so the
# heuristic actually fires regardless of which script a given utterance
# comes back in.
_TRAILING_INCOMPLETE_WORDS = {
    "and", "but", "so", "or", "because",
    "aur", "lekin", "toh", "ki", "kyunki", "matlab",
    "और", "लेकिन", "तो", "की", "क्योंकि", "मतलब",
}

# Played at most once per turn, only on the first extension -- a repeated or
# every-pause filler would itself become the annoying robotic pattern this
# feature is trying to avoid (see system_prompt.py's filler-word rule for the
# same "sparingly, vary it" principle applied to spoken replies).
_EXTENSION_FILLERS = ["Mm-hmm...", "Go ahead...", "I'm listening..."]


def _is_incomplete(text: str) -> bool:
    """Fast, pure-string heuristic -- no LLM call, so it can't add latency."""
    stripped = text.strip()
    if not stripped:
        return True
    words = stripped.rstrip(".,!?").split()
    if not words:
        return True
    if words[-1].lower() in _TRAILING_INCOMPLETE_WORDS:
        return True
    if stripped.endswith(","):
        return True
    if len(words) < 3 and stripped[-1] not in ".!?":
        return True
    return False


class HybridCompletenessUserTurnStopStrategy(BaseUserTurnStopStrategy):
    """VAD-driven turn stop with a transcript-completeness extension.

    After VAD detects the user stopped speaking, waits `base_timeout`
    seconds (same policy floor as production's SpeechTimeoutUserTurnStopStrategy).
    When that elapses, checks whether the accumulated transcript looks
    mid-sentence. If so, and the hard cap (`max_wait`, measured from the
    original VAD-stop moment) hasn't been reached, waits `extension_timeout`
    more seconds instead of firing -- optionally speaking one short filler
    on the first such extension. Otherwise fires immediately, exactly like
    the production strategy.
    """

    def __init__(
        self,
        *,
        base_timeout: float = 0.9,
        extension_timeout: float = 0.7,
        max_wait: float = 2.8,
        wait_for_transcript: bool = True,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._base_timeout = base_timeout
        self._extension_timeout = extension_timeout
        self._max_wait = max_wait
        self._wait_for_transcript = wait_for_transcript

        self._stt_timeout: float = 0.0
        self._stop_secs: float = 0.0

        self._text = ""
        self._vad_user_speaking = False
        self._transcript_finalized = False
        self._vad_stopped_at: float | None = None  # time.monotonic(), for the hard cap

        self._speech_timer_task: asyncio.Task | None = None
        self._stt_timeout_task: asyncio.Task | None = None
        self._user_speech_wait_done = False
        self._stt_wait_done = False
        self._extended_once = False

    async def reset(self):
        await super().reset()
        self._text = ""
        self._vad_user_speaking = False
        self._transcript_finalized = False
        self._vad_stopped_at = None
        self._user_speech_wait_done = False
        self._stt_wait_done = False
        self._extended_once = False
        await self._cancel_all_tasks()

    async def cleanup(self):
        await super().cleanup()
        await self._cancel_all_tasks()

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, STTMetadataFrame):
            self._stt_timeout = frame.ttfs_p99_latency
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            await self._handle_vad_user_started_speaking()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            await self._handle_vad_user_stopped_speaking(frame)
        elif isinstance(frame, TranscriptionFrame):
            await self._handle_transcription(frame)
        return ProcessFrameResult.CONTINUE

    async def _handle_vad_user_started_speaking(self):
        self._vad_user_speaking = True
        self._transcript_finalized = False
        self._vad_stopped_at = None
        self._user_speech_wait_done = False
        self._stt_wait_done = False
        self._extended_once = False
        await self._cancel_all_tasks()

    async def _handle_vad_user_stopped_speaking(self, frame: VADUserStoppedSpeakingFrame):
        self._vad_user_speaking = False
        self._stop_secs = frame.stop_secs
        self._vad_stopped_at = time.monotonic()
        await self._restart_speech_timer(self._base_timeout)

        effective_stt_wait = max(0.0, self._stt_timeout - self._stop_secs)
        self._stt_wait_done = self._transcript_finalized or effective_stt_wait <= 0
        if not self._stt_wait_done:
            self._stt_timeout_task = self.task_manager.create_task(
                self._stt_timeout_handler(effective_stt_wait), f"{self}::_stt_timeout_handler"
            )

    async def _handle_transcription(self, frame: TranscriptionFrame):
        self._text += frame.text
        if frame.finalized:
            self._transcript_finalized = True
            if not self._stt_wait_done:
                self._stt_wait_done = True
                if self._stt_timeout_task:
                    await self.task_manager.cancel_task(self._stt_timeout_task)
                    self._stt_timeout_task = None
        if self._user_speech_wait_done and self._stt_wait_done:
            await self._maybe_trigger_user_turn_stopped()

    async def _restart_speech_timer(self, timeout: float):
        if self._speech_timer_task:
            await self.task_manager.cancel_task(self._speech_timer_task)
            self._speech_timer_task = None
        self._user_speech_wait_done = False
        self._speech_timer_task = self.task_manager.create_task(
            self._speech_timer_handler(timeout), f"{self}::_speech_timer_handler"
        )

    async def _speech_timer_handler(self, timeout: float):
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            return
        finally:
            self._speech_timer_task = None

        if self._vad_user_speaking:
            return  # guest resumed talking before this timer fired

        elapsed = time.monotonic() - self._vad_stopped_at if self._vad_stopped_at else self._max_wait
        if _is_incomplete(self._text) and elapsed < self._max_wait:
            if not self._extended_once:
                self._extended_once = True
                try:
                    await self.push_frame(
                        TTSSpeakFrame(random.choice(_EXTENSION_FILLERS)), FrameDirection.DOWNSTREAM
                    )
                except Exception:
                    # Unverified whether push_frame from a turn-stop strategy
                    # reliably reaches TTS -- silence-extension (below) is
                    # the actual feature; the filler is best-effort only.
                    logger.debug(f"{self}: extension filler could not be sent, continuing silently")
            await self._restart_speech_timer(self._extension_timeout)
            return

        self._user_speech_wait_done = True
        await self._maybe_trigger_user_turn_stopped()

    async def _stt_timeout_handler(self, timeout: float):
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            return
        finally:
            self._stt_timeout_task = None
        self._stt_wait_done = True
        await self._maybe_trigger_user_turn_stopped()

    async def _maybe_trigger_user_turn_stopped(self):
        if self._vad_user_speaking:
            return
        if self._wait_for_transcript and not self._text:
            return
        if self._user_speech_wait_done and self._stt_wait_done:
            await self.trigger_user_turn_stopped()

    async def _cancel_all_tasks(self):
        if self._speech_timer_task:
            await self.task_manager.cancel_task(self._speech_timer_task)
            self._speech_timer_task = None
        if self._stt_timeout_task:
            await self.task_manager.cancel_task(self._stt_timeout_task)
            self._stt_timeout_task = None
