"""Code-level backstop for the "let me loop in the host" ban in
GOLDEN_RULES (system_prompt.py).

Originally a regex that rewrote the reply only if it matched a banned
"loop...host" phrase. That approach was confirmed live to be a genuine
whack-a-mole problem, not a one-time fix: the model has been caught saying
"let me loop in the host", "let me open the host", and other live variants
that don't share a common regex-matchable shape -- each fix narrowed to the
specific wording already seen, and the next call surfaced a new phrasing the
regex didn't cover. Prompting alone doesn't reliably prevent this either
(GOLDEN_RULES already banned the "loop in the host" phrase by name).

Given a growing, open-ended set of ways the model can phrase "I'm bringing
the host into this call right now" -- which is never correct; a booking
confirmation always means the host follows up later, not that they join
live -- this guard no longer tries to detect bad phrasings at all. It
unconditionally replaces the ENTIRE first text-bearing reply after
escalate_to_host fires with SAFE_REPLACEMENT_TEXT, regardless of what the
model actually said. This is the only way to guarantee no phrasing variant
can slip through: there is no detection step left to have a gap in.

Scoped narrowly on purpose: only the first text-bearing LLM response at or
after an escalate_to_host call is buffered/replaced. Every other turn in the
call passes straight through unmodified and unbuffered -- no added latency
to the common case.

Arms itself by watching for FunctionCallsStartedFrame naming
escalate_to_host, not via an arm() called from the tool's own Python code.
Confirmed live 2026-07-26: the model generates the tool call AND its own
text in the SAME completion, microseconds apart -- an arm() invoked from
inside the tool-execution handler races against that same completion's text
already flowing through this processor and can lose. Watching the frame
directly in this processor's own stream removes the race entirely, since
FunctionCallsStartedFrame and the response's LLMFullResponseStartFrame both
flow through this exact position, in order, with no cross-stream timing
involved.
"""

from pipecat.frames.frames import (
    Frame,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

SAFE_REPLACEMENT_TEXT = (
    "Okay, I've noted your details -- the host will follow up with you on WhatsApp shortly to confirm."
)


class EscalationPhraseGuardProcessor(FrameProcessor):
    """Unconditionally replaces the one LLM response right after
    escalate_to_host fires with SAFE_REPLACEMENT_TEXT -- see module docstring
    for why detecting bad phrasings first was abandoned."""

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
                # A tool-call-only cycle with no text of its own -- stays
                # armed for the next cycle, same as before this frame.
                await self.push_frame(frame, direction)
                return

            # No detection step -- see module docstring. Every reply here
            # becomes SAFE_REPLACEMENT_TEXT, regardless of what the model
            # actually said.
            self._armed = False
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame(SAFE_REPLACEMENT_TEXT))
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
