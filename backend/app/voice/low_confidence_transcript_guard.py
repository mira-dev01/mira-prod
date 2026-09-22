"""Catches a transcript Sarvam itself wasn't confident about and asks the
guest to repeat, instead of letting the LLM silently treat a garbled
transcript as ground truth.

Confirmed live: a guest's location got dropped entirely by STT ("के लिए लगा
के हिसाब से" instead of whatever was actually said, e.g. "Delhi ke hisaab
se...") on a hi-IN utterance Sarvam itself only tagged with
language_probability=0.843. The transcript was non-blank and well-formed
enough to pass SilenceWatchdogProcessor's blank-only filter and the turn-stop
strategy's wait_for_transcript gate, so it reached the LLM looking like a
genuine, complete guest answer. recommend_properties then received
preferred_location=None -- a CORRECT extraction from what the LLM was given,
not a bug in the tool or its filters. The actual failure was upstream: no
processor between stt and the LLM context ever inspected whether Sarvam
itself was unsure about the utterance.

language_probability is Sarvam's confidence in which LANGUAGE it detected
(hi-IN vs. en-IN vs. ...), not a direct transcript-accuracy score -- see
config.py's sarvam_vad_* comment block, which explicitly calls this out and
declines to use it as a noise-rejection signal for exactly that reason. This
processor uses it anyway as a proxy: in practice, Sarvam being unsure what
language it's even hearing correlates with the transcript for that utterance
being unreliable (this module exists because of one such confirmed case), and
"ask the guest to repeat" costs an extra conversational turn on a false
positive but no correctness risk, unlike using the same threshold as a hard
noise-reject would. Deliberately narrower than that VAD tuning: this only
ever asks for clarification, never drops/ignores audio.

Sits right after stt, before silence_watchdog, language_sync, and the user
aggregator -- so a substituted clarification line never reaches
ConversationStyle's language-family tracking, LanguageSyncProcessor's TTS
language switch, or the LLM context as if it were the guest's real words, and
so silence_watchdog's repetition-shadow/strike-counter logic sees the
clarification exchange as an ordinary completed turn (a real TranscriptionFrame
still flows downstream, just with substituted text), not as silence.

Deterministic substitution only -- no LLM call, matching CLAUDE.md's
"Validators must not introduce hidden LLM regeneration" invariant. The
low-confidence TranscriptionFrame's text is replaced in place with a fixed
clarification request before it's forwarded; nothing re-invokes the LLM to
"fix" or reinterpret the garbled transcript.
"""

from loguru import logger

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Starting point, not yet validated against a corpus of real call transcripts
# with known-correct ground truth -- there is no existing baseline in this
# repo to derive a specific number from (same caveat sarvam_vad_* in
# config.py documents for itself). Sarvam's docs describe language_probability
# as 0.0-1.0 with higher = more confident; 0.85 is set just above the
# confirmed live failure case (language_probability=0.843) so that exact
# transcript would have triggered clarification instead of silently reaching
# the LLM as ground truth. Revisit against real production language_probability
# distributions once logged (this processor logs every value observed, see
# below) -- 0.843 being this close to a round 0.85 is coincidence, not
# evidence the boundary is precisely tuned.
DEFAULT_LANGUAGE_PROBABILITY_THRESHOLD = 0.85

DEFAULT_CLARIFICATION_TEXT = "Sorry, I didn't quite catch that -- could you say that again?"


class LowConfidenceTranscriptGuardProcessor(FrameProcessor):
    """Substitutes a clarification request for any transcript Sarvam itself
    flagged as low language-detection confidence, before it reaches the LLM.
    """

    def __init__(
        self,
        *,
        threshold: float = DEFAULT_LANGUAGE_PROBABILITY_THRESHOLD,
        clarification_text: str = DEFAULT_CLARIFICATION_TEXT,
    ):
        super().__init__()
        self._threshold = threshold
        self._clarification_text = clarification_text

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.text and frame.text.strip():
            probability = self._extract_language_probability(frame)
            if probability is not None:
                # Phase-equivalent observability discipline to silence_watchdog.py:
                # log the score itself (not guest content) on every transcript so
                # the threshold above can eventually be validated against real
                # production data.
                logger.debug(
                    "stt_language_probability_observed language_probability={} transcript_chars={}",
                    probability,
                    len(frame.text.strip()),
                )
                if probability < self._threshold:
                    logger.info(
                        "low_confidence_transcript_guard_triggered language_probability={} threshold={}",
                        probability,
                        self._threshold,
                    )
                    # Deterministic substitution, not a second LLM call -- the
                    # guest's actual (possibly-wrong) words never reach the LLM
                    # context; the clarification request is what gets recorded
                    # as this turn's transcript instead.
                    frame.text = self._clarification_text

        await self.push_frame(frame, direction)

    def _extract_language_probability(self, frame: TranscriptionFrame) -> float | None:
        """Pulls language_probability out of the raw Sarvam response attached
        to frame.result (pipecat.services.sarvam.stt._handle_message sets
        this to message.dict() -- see SpeechToTextTranscriptionData's own
        schema in the sarvamai SDK). Returns None if unavailable (e.g. a
        fixed, non-"unknown" language_code was configured, wrong STT
        provider, or the field is genuinely absent) -- this processor is a
        pure no-op in that case, same fail-open discipline the rest of the
        pipeline's optional signals use.
        """
        result = frame.result
        if not isinstance(result, dict):
            return None
        data = result.get("data")
        if not isinstance(data, dict):
            return None
        probability = data.get("language_probability")
        if isinstance(probability, (int, float)):
            return float(probability)
        return None
