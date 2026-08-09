"""Code-level backstop guaranteeing end_call actually fires whenever the
model speaks the closing line.

Confirmed live: a guest-initiated close ("thank you" with no host prompt)
repeatedly produced a completion with DEFAULT_CLOSING_PHRASE
(system_prompt.py) as spoken text but NO end_call tool call in the same
completion -- system_prompt.py's own closing-phrasing rule instructs the
model to say the line AND call end_call "in that same turn," but nothing
upstream of tools.py enforces that pairing; the model can (and, confirmed
live, sometimes does) speak the line without emitting the tool call. Guest-
confirmed closes reached via "anything else?" -> "no" apparently pair the two
reliably; a bare, guest-initiated "thank you" close was the failing case.

This is the same category of failure escalation_phrase_guard.py already
solved for a different symptom (the model's phrasing of the escalation line
varying in ways no regex could keep up with) -- but the fix shape here is
different because the fact being guaranteed is different. escalation_phrase_
guard replaces TEXT after a tool call it trusts already happened; this
processor guarantees a TOOL EFFECT after text it can check deterministically,
because DEFAULT_CLOSING_PHRASE is a fixed backend constant (unlike
agent_escalation_phrase, it has no per-host override -- see
_persona_and_escalation_sections in system_prompt.py), so matching it is a
hand-it-a-fact check, not a guessed heuristic about what "sounds like" an
ending.

Design: sits between llm and tts, right where premature_end_call_guard
(same-turn question-bundling backstop for the opposite failure) already
watches FunctionCallsStartedFrame for end_call. Streams every frame straight
through unmodified and immediately -- no buffering delay, no rewriting, zero
added latency to the normal case. Only at LLMFullResponseEndFrame does it
check: did this response's text normalize-match DEFAULT_CLOSING_PHRASE, AND
did end_call NOT fire this same cycle? If both, it calls
silence_watchdog.request_end_after_current_turn() directly -- the exact same
call tools.py's end_call wrapper makes -- so the hangup arms exactly as if
the tool call had actually happened. No extra LLM call, no retry, no new
frame types.

Normalization mirrors repetition_guard.py's own _normalize (lowercase,
collapse whitespace, strip non-word characters) rather than an exact string
match -- TTS-bound text can vary in trailing punctuation/dash rendering
("--" vs "—") without being a different sentence.
"""

import re

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.prompts.system_prompt import DEFAULT_CLOSING_PHRASE
from app.voice.silence_watchdog import SilenceWatchdogProcessor

_NON_WORD_RE = re.compile(r"[^\w\s]")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", _NON_WORD_RE.sub(" ", text.lower())).strip()


_NORMALIZED_CLOSING_PHRASE = _normalize(DEFAULT_CLOSING_PHRASE)


class EndCallReliabilityGuardProcessor(FrameProcessor):
    """Arms the hangup itself if the model spoke the closing line without
    also calling end_call this same turn."""

    def __init__(self, silence_watchdog: SilenceWatchdogProcessor):
        super().__init__()
        self._silence_watchdog = silence_watchdog
        self._end_call_seen = False
        self._buffer: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, FunctionCallsStartedFrame):
            if any(fc.function_name == "end_call" for fc in frame.function_calls):
                self._end_call_seen = True
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffer = []
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMTextFrame):
            self._buffer.append(frame.text)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            end_call_seen = self._end_call_seen
            self._end_call_seen = False
            spoke_closing_line = _NORMALIZED_CLOSING_PHRASE in _normalize("".join(self._buffer))
            self._buffer = []

            if spoke_closing_line and not end_call_seen:
                logger.warning(
                    "EndCallReliabilityGuardProcessor: model spoke the closing line without "
                    "calling end_call this turn -- arming the hangup directly"
                )
                await self._silence_watchdog.request_end_after_current_turn()

            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
