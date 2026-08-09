"""Code-level backstop for GOLDEN_RULES' ban on narrator/stage-direction text
(system_prompt.py: "This also covers narrator/meta text that isn't a turn
label but is just as out of place on a phone call -- things like
'This is the end.'... 'End of call.'...").

Confirmed live 2026-07-27, a new variant that rule doesn't literally cover:
"(Waiting for guest response)" was spoken/shown as part of a reply, right
after a real question -- the model narrating its own conversational state
in a parenthetical instead of just asking the question and stopping. Same
underlying failure as the phrases GOLDEN_RULES already bans (the model
describing the call instead of just having it), just a new shape -- prompt
wording alone hasn't reliably prevented this category, matching every other
guard in this file's package.

Design: forwards text immediately by default -- zero added latency to the
common case, which never opens a "(" at all. Only once an opening "(" is
seen does it start holding text back, and only until the matching ")"
closes (or a generous max length is exceeded, treated as "not a short
meta-comment, flush it"). The held span is dropped entirely if it matches
a known meta-commentary shape, otherwise forwarded unchanged -- legitimate
parenthetical content (e.g. a price aside) is untouched.
"""

import re

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# A short parenthetical describing the AI's own conversational state/process
# rather than real spoken content -- narrating, not talking.
_META_COMMENTARY_RE = re.compile(
    r"\b(waiting|listening|pause|paused|silence|thinking|typing|processing|no response)\b", re.IGNORECASE
)
# Above this, a "(" almost certainly opened legitimate content (a long aside,
# an address, a list) rather than a short stage direction -- stop holding and
# just flush it as normal text.
_MAX_HOLD_CHARS = 80


class MetaCommentaryGuardProcessor(FrameProcessor):
    """Drops parenthetical narrator/stage-direction asides like "(Waiting
    for guest response)" before they reach TTS -- every other turn passes
    through unbuffered."""

    def __init__(self):
        super().__init__()
        self._held: str | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._held = None
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMTextFrame):
            await self._consume(frame.text)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            if self._held:
                # Response ended mid-parenthetical -- never resolved to a
                # close, so it was never actually judged. Flush as-is rather
                # than silently eating it.
                await self.push_frame(LLMTextFrame(self._held))
                self._held = None
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    async def _consume(self, text: str) -> None:
        if self._held is not None:
            # Already inside an open "(" from an earlier chunk -- merge, then
            # fall through to the close-search loop below. Merging BEFORE
            # searching matters: the close paren can arrive in the same
            # chunk as the merge, and searching self._held alone (pre-merge)
            # would miss it.
            self._held += text
        else:
            idx = text.find("(")
            if idx == -1:
                await self.push_frame(LLMTextFrame(text))
                return
            if idx > 0:
                await self.push_frame(LLMTextFrame(text[:idx]))
            self._held = text[idx:]

        # A single chunk can contain an entire "(...)" (or even several) on
        # its own -- keep resolving until nothing held is left to judge.
        while self._held is not None:
            close_idx = self._held.find(")")
            if close_idx == -1:
                if len(self._held) > _MAX_HOLD_CHARS:
                    await self.push_frame(LLMTextFrame(self._held))
                    self._held = None
                return

            span = self._held[: close_idx + 1]
            rest = self._held[close_idx + 1 :]
            self._held = None
            if _META_COMMENTARY_RE.search(span):
                logger.warning(
                    "MetaCommentaryGuardProcessor: dropped a narrator/stage-direction "
                    "parenthetical before TTS"
                )
            else:
                await self.push_frame(LLMTextFrame(span))

            if not rest:
                return
            idx = rest.find("(")
            if idx == -1:
                await self.push_frame(LLMTextFrame(rest))
                return
            if idx > 0:
                await self.push_frame(LLMTextFrame(rest[:idx]))
            self._held = rest[idx:]
