"""Code-level backstop for a call ending itself without ever giving the
guest a real chance to answer.

GOLDEN_RULES already says a question must be the ENTIRE reply for that turn
("Speak only your own single turn, then stop and wait... If you ask a
question, your turn ends at that question mark"), but the model doesn't
always comply. Confirmed live 2026-07-26: one single completion produced
"Exactly, about an hour and fifteen minutes from the airport... Anything
else you'd like to sort out? Thanks so much for calling -- have a wonderful
day!" AND called end_call, all in the same turn -- the guest never got a
real window to answer "anything else?" before the call hung up. Distinct
from the redundant-re-invocation bug (redundant_context_guard.py): here
there was only ONE LLM completion, not two -- this is the model bundling
too much into a single turn, not the pipeline invoking it spuriously.

Sits between llm and tts (same position as escalation_phrase_guard). Does
NOT withhold or rewrite any frames -- the text is fine and should be spoken
as-is. It only watches: did this same turn both ask a real question AND call
end_call? If so, it cancels the pending hangup via
silence_watchdog.cancel_end_request() -- the question still gets asked, but
the call falls through to the normal silence-nudge path instead of ending the
instant that line finishes playing, giving the guest an actual chance to reply.

Frame order matters. In pipecat 1.6.0 a completion that both speaks and calls
a tool emits LLMFullResponseStartFrame, the LLMTextFrames, then
FunctionCallsStartedFrame (the tool runs), then LLMFullResponseEndFrame. The
question is therefore ALREADY in the past when end_call shows up, so the text of
every response is buffered from its start, never only after end_call is seen.
(An earlier version began buffering at FunctionCallsStartedFrame, saw an empty
buffer in production, and never cancelled anything; its tests sent the frames in
an order the pipeline never produces.)

What counts as a question: a question mark (Latin, fullwidth or Arabic), or an
open offer worded without one -- "anything else", "aur kuch", "और कुछ" -- since
spoken Hindi/Hinglish replies are often written without punctuation.
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

from app.voice.silence_watchdog import SilenceWatchdogProcessor

_QUESTION_MARKS = ("?", "\uff1f", "\u061f")

# Open offers that invite an answer even when the text has no question mark.
_OPEN_OFFER_RE = re.compile(
    r"anything\s+else|something\s+else|any\s+other\s+(?:question|help|doubt)"
    r"|aur\s+kuch|kuch\s+aur|koi\s+aur\s+(?:sawal|question|madad|help)"
    r"|\u0914\u0930\s+\u0915\u0941\u091b|\u0915\u0941\u091b\s+\u0914\u0930",
    re.IGNORECASE,
)


def asks_open_question(text: str) -> bool:
    """True when the spoken text leaves the guest an open question to answer."""
    return any(mark in text for mark in _QUESTION_MARKS) or _OPEN_OFFER_RE.search(text) is not None


class PrematureEndCallGuardProcessor(FrameProcessor):
    """Cancels a same-turn end_call if that turn also asked the guest a
    real question."""

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
        elif isinstance(frame, LLMFullResponseStartFrame):
            # Every response is buffered from its first frame; the end_call
            # flag is NOT reset here because a frame source that announces the
            # tool call first (as this module's older tests did) must still work.
            self._buffer = []
            self._silence_watchdog.note_reply_asked_question(False)
        elif isinstance(frame, LLMTextFrame):
            self._buffer.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            end_call_seen, self._end_call_seen = self._end_call_seen, False
            text, self._buffer = "".join(self._buffer), []
            asked = asks_open_question(text)
            # end_call runs in a background task, so it may arm the hangup after this
            # frame is seen: remember the question so a late arm is refused as well.
            self._silence_watchdog.note_reply_asked_question(asked)
            if end_call_seen and asked:
                logger.warning(
                    "PrematureEndCallGuardProcessor: cancelling the pending hangup -- this turn also "
                    "asked the guest a real question"
                )
                self._silence_watchdog.cancel_end_request()

        await self.push_frame(frame, direction)
