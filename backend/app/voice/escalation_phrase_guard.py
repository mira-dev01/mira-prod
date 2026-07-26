"""Code-level backstop for the "let me loop in the host" ban in
GOLDEN_RULES (system_prompt.py).

Prompting alone has failed on this exact wording repeatedly across real
calls despite the rule citing this precise phrase as its own confirmed-live
counterexample -- a known limit of instruction-following, not something a
bigger prompt reliably fixes on its own. This processor is the backstop,
not a replacement for the rule.

Scoped narrowly on purpose: it only buffers and scans the first text-bearing
LLM response at or after an escalate_to_host call. Every other turn in the
call passes straight through unmodified and unbuffered -- no added latency
to the common case. Buffering the full response before release does add
latency to the one turn it's active for (can't scan text that hasn't
arrived yet), which is an acceptable trade only because it's rare and
specifically where the failure has actually happened.

Arms itself by watching for FunctionCallsStartedFrame naming
escalate_to_host, not via an arm() called from the tool's own Python code.
Confirmed live 2026-07-26: the model generates the tool call AND the banned
text in the SAME completion, microseconds apart -- an arm() invoked from
inside the tool-execution handler races against that same completion's text
already flowing through this processor and can lose. Watching the frame
directly in this processor's own stream removes the race entirely, since
FunctionCallsStartedFrame and the response's LLMFullResponseStartFrame both
flow through this exact position, in order, with no cross-stream timing
involved.
"""

import re

from pipecat.frames.frames import (
    Frame,
    FunctionCallsStartedFrame,
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

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, FunctionCallsStartedFrame):
            if any(fc.function_name == "escalate_to_host" for fc in frame.function_calls):
                # Stays armed across a tool-call-only cycle with no text of
                # its own (this frame's own cycle) -- only a cycle that
                # actually contains text disarms it, in the block below.
                self._armed = True
            await self.push_frame(frame, direction)
            return

        if not self._armed:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffering = True
            self._buffer = []
            return

        if self._buffering and isinstance(frame, LLMTextFrame):
            self._buffer.append(frame.text)
            return

        if self._buffering and isinstance(frame, LLMFullResponseEndFrame):
            text = "".join(self._buffer)
            self._buffering = False

            if not text.strip():
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
