"""Code-level backstop for the "let me loop in the host" ban in
GOLDEN_RULES (system_prompt.py).

Prompting alone has failed on this exact wording repeatedly across real
calls despite the rule citing this precise phrase as its own confirmed-live
counterexample -- a known limit of instruction-following, not something a
bigger prompt reliably fixes on its own. This processor is the backstop,
not a replacement for the rule.

Scoped narrowly on purpose: it only buffers and scans the first text-bearing
LLM response at or after an escalate_to_host call, via arm(). Every other
turn in the call passes straight through unmodified and unbuffered -- no
added latency to the common case. Buffering the full response before
release does add latency to the one turn it's active for (can't scan text
that hasn't arrived yet), which is an acceptable trade only because it's
rare and specifically where the failure has actually happened.
"""

import re

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

_BANNED_PHRASE_RE = re.compile(r"loop\w*.{0,15}host|host.{0,15}loop", re.IGNORECASE)
SAFE_REPLACEMENT_TEXT = "The host will follow up with you on WhatsApp shortly."


class EscalationPhraseGuardProcessor(FrameProcessor):
    """Buffers and rewrites the one LLM response right after escalate_to_host
    fires, if it contains a banned "loop in the host" variant."""

    def __init__(self):
        super().__init__()
        self._armed = False
        self._buffering = False
        self._buffer: list[str] = []
        self._start_frame: LLMFullResponseStartFrame | None = None

    def arm(self) -> None:
        """Called by the escalate_to_host tool (app/voice/tools.py) the
        moment it fires. Stays armed across an empty (tool-call-only)
        response cycle -- only a cycle that actually contains text disarms
        it -- so it catches the reply whether the model speaks in the same
        completion as the tool call or the very next one."""
        self._armed = True

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if not self._armed:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffering = True
            self._buffer = []
            self._start_frame = frame
            return

        if self._buffering and isinstance(frame, LLMTextFrame):
            self._buffer.append(frame.text)
            return

        if self._buffering and isinstance(frame, LLMFullResponseEndFrame):
            text = "".join(self._buffer)
            self._buffering = False
            self._start_frame = None

            if not text.strip():
                # Tool-call-only cycle, nothing spoken -- stay armed for the
                # next (likely text-bearing) cycle instead of disarming here.
                await self.push_frame(frame, direction)
                return

            self._armed = False
            if _BANNED_PHRASE_RE.search(text):
                # Replace the whole utterance, not just the matched phrase --
                # a surgical substring swap risks leaving a grammatically
                # broken fragment around it (e.g. "Okay, so <replacement>
                # directly!"). The confirmed failures are short, single-idea
                # sentences, so a full swap reads cleanly.
                text = SAFE_REPLACEMENT_TEXT

            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame(text))
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
