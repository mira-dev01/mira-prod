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
import time

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    STTMetadataFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_stop.base_user_turn_stop_strategy import BaseUserTurnStopStrategy

# Trailing words that suggest the sentence isn't over yet, in the languages
# guests actually mix mid-call (see system_prompt.py's Hinglish rule).
# Sarvam STT (mode="codemix", app/voice/pipeline.py) transcribes Hindi in
# Devanagari script, not romanized -- both spellings are listed so the
# heuristic actually fires regardless of which script a given utterance
# comes back in. Determiners/prepositions ("that", "the", "in", ...) added
# after a live call was cut off exactly on "...stay in that" -- a sentence
# should essentially never legitimately end there, but the original list
# only covered conjunctions, so nothing caught it.
_TRAILING_INCOMPLETE_WORDS = {
    "and", "but", "so", "or", "because",
    "aur", "lekin", "toh", "ki", "kyunki", "matlab",
    "और", "लेकिन", "तो", "की", "क्योंकि", "मतलब",
    "the", "a", "an", "this", "that", "these", "those",
    "my", "your", "his", "her", "its", "our", "their",
    "in", "at", "on", "to", "for", "with", "about", "of",
}


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
    # "?"/"!" are deliberate, confident completeness signals (rising
    # intonation / emphasis) that STT only adds when it's confident -- trust
    # them even on a short utterance ("Kasauli?", "Yes!"). A lone "." is
    # Sarvam's default/ambiguous sentence-final punctuation and gets added
    # on almost any falling-pitch pause, including mid-thought (confirmed
    # live: "...my phone number is 9." read as complete on a single digit).
    # It should not by itself rescue a short answer from the length check.
    if len(words) < 3 and stripped[-1] not in "!?":
        return True
    return False


class HybridCompletenessUserTurnStopStrategy(BaseUserTurnStopStrategy):
    """VAD-driven turn stop with a transcript-completeness extension.

    After VAD detects the user stopped speaking, waits `base_timeout`
    seconds (same policy floor as production's SpeechTimeoutUserTurnStopStrategy).
    When that elapses, checks whether the accumulated transcript looks
    mid-sentence. If so, and the hard cap (`max_wait`, measured from the
    original VAD-stop moment) hasn't been reached, waits `extension_timeout`
    more seconds instead of firing, silently -- no spoken filler (see
    _speech_timer_handler for why that was removed). Otherwise fires
    immediately, exactly like the production strategy.
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
        self._turn_stopped_fired = False

    async def reset(self):
        await super().reset()
        self._text = ""
        self._vad_user_speaking = False
        self._transcript_finalized = False
        self._vad_stopped_at = None
        self._user_speech_wait_done = False
        self._stt_wait_done = False
        self._turn_stopped_fired = False
        await self._cancel_all_tasks()

    async def cleanup(self):
        await super().cleanup()
        await self._cancel_all_tasks()

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        logger.debug(f"{self}: [DEBUGTURN] process_frame received {type(frame).__name__}")
        if isinstance(frame, STTMetadataFrame):
            self._stt_timeout = frame.ttfs_p99_latency
            logger.debug(f"{self}: [DEBUGTURN] STTMetadataFrame stt_timeout={self._stt_timeout}")
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            await self._handle_vad_user_started_speaking()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            await self._handle_vad_user_stopped_speaking(frame)
        elif isinstance(frame, TranscriptionFrame):
            await self._handle_transcription(frame)
        return ProcessFrameResult.CONTINUE

    async def _handle_vad_user_started_speaking(self):
        logger.debug(f"{self}: [DEBUGTURN] VAD started speaking")
        self._vad_user_speaking = True
        self._transcript_finalized = False
        self._vad_stopped_at = None
        self._user_speech_wait_done = False
        self._stt_wait_done = False
        self._turn_stopped_fired = False
        await self._cancel_all_tasks()

    async def _handle_vad_user_stopped_speaking(self, frame: VADUserStoppedSpeakingFrame):
        logger.debug(f"{self}: [DEBUGTURN] VAD stopped speaking, stop_secs={frame.stop_secs}, base_timeout={self._base_timeout}")
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
        logger.debug(f"{self}: [DEBUGTURN] transcription received: text={self._text!r} finalized={frame.finalized}")
        if frame.finalized:
            self._transcript_finalized = True
            if not self._stt_wait_done:
                self._stt_wait_done = True
                if self._stt_timeout_task:
                    await self.task_manager.cancel_task(self._stt_timeout_task)
                    self._stt_timeout_task = None
        if self._user_speech_wait_done and self._stt_wait_done:
            await self._maybe_trigger_user_turn_stopped()
            return

        # Fallback (mirrors pipecat's own SpeechTimeoutUserTurnStopStrategy):
        # a transcript can arrive before VADUserStoppedSpeakingFrame does --
        # confirmed via live testing, where VAD-stop never fired for a
        # synthetic silence gap and the turn only ended via pipecat's
        # generic ~5s stuck-turn watchdog instead of this strategy's own
        # 0.9s-2.8s adaptive logic. Without this branch, a transcript with
        # no corresponding VAD-stop event never starts the completeness
        # timer at all -- the strategy just waits forever. stt_timeout has
        # no meaning without a VAD-stop timestamp to measure from, so mark
        # it done immediately and drive completion off the speech timer.
        if not self._vad_user_speaking and self._vad_stopped_at is None:
            logger.debug(f"{self}: [DEBUGTURN] fallback: transcript with no prior VAD-stop, starting timer")
            self._stt_wait_done = True
            self._vad_stopped_at = time.monotonic()
            await self._restart_speech_timer(self._base_timeout)

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
        # A VAD-stop with no transcript at all (breath, mic click, ambient
        # noise, or the guest hanging up) isn't "mid-sentence" -- there's no
        # sentence to be mid- of. Confirmed live: without this guard,
        # _is_incomplete("") -> True unconditionally (its own first check)
        # made this fire the extension+filler on pure silence, producing a
        # nonsensical trailing "Go ahead..." with nothing before or after it
        # (seen right after a guest said "Bye"). Genuine "STT just hasn't
        # delivered yet" is a different, already-handled case --
        # _stt_timeout_handler/effective_stt_wait, not this heuristic.
        incomplete = bool(self._text.strip()) and _is_incomplete(self._text)
        logger.debug(
            f"{self}: [DEBUGTURN] speech timer fired: text={self._text!r} incomplete={incomplete} "
            f"elapsed={elapsed:.2f} max_wait={self._max_wait}"
        )
        if incomplete and elapsed < self._max_wait:
            # No longer speaking a filler here -- confirmed live that
            # push_frame(TTSSpeakFrame(...)) from a turn-stop strategy isn't
            # ordered against the main LLM->TTS flow: on one call it landed
            # mid-sentence inside an unrelated, already-in-progress assistant
            # reply ("...well within the limit.Go.. .Is there anything
            # else..."), corrupting it. The silent wait-extension below is
            # what this strategy is actually for; the spoken filler was
            # explicitly "best-effort only" (see prior comment here) and
            # this is worse than the dead air it was trying to cover.
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
        logger.debug(
            f"{self}: [DEBUGTURN] maybe_trigger check: vad_speaking={self._vad_user_speaking} "
            f"text={self._text!r} speech_wait_done={self._user_speech_wait_done} stt_wait_done={self._stt_wait_done} "
            f"already_fired={self._turn_stopped_fired}"
        )
        if self._turn_stopped_fired:
            # Confirmed live: Sarvam can emit a late/corrected finalized
            # TranscriptionFrame for the same utterance after this turn
            # already fired once (more likely on a hesitant, multi-clause
            # utterance -- "Yeah, yeah. So my phone number is 9."). Without
            # this guard, _handle_transcription's fast-path (both wait flags
            # already True from the first firing) calls back in here and
            # re-triggers a second, redundant LLM turn on almost the same
            # context -- the model doesn't error, it just answers again with
            # a slightly different phrasing, which is what produced several
            # paraphrased "what's the phone number" replies stacked with no
            # real user turn between them. Only a fresh VADUserStartedSpeaking
            # (or reset()) clears this, since that's the only real signal a
            # new logical turn has actually started.
            return
        if self._vad_user_speaking:
            return
        if self._wait_for_transcript and not self._text:
            return
        if self._user_speech_wait_done and self._stt_wait_done:
            logger.debug(f"{self}: [DEBUGTURN] FIRING trigger_user_turn_stopped()")
            self._turn_stopped_fired = True
            await self.trigger_user_turn_stopped()

    async def _cancel_all_tasks(self):
        if self._speech_timer_task:
            await self.task_manager.cancel_task(self._speech_timer_task)
            self._speech_timer_task = None
        if self._stt_timeout_task:
            await self.task_manager.cancel_task(self._stt_timeout_task)
            self._stt_timeout_task = None
