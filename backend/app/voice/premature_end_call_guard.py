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
as-is. It only watches: did this same turn both ask a real question (a "?"
anywhere in the text) AND call end_call? If so, it cancels the pending
hangup via silence_watchdog.cancel_end_request() -- the question still gets
asked, but the call falls through to the normal silence-nudge path instead
of ending the instant that line finishes playing, giving the guest an
actual chance to reply.
"""

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


class PrematureEndCallGuardProcessor(FrameProcessor):
    """Cancels a same-turn end_call if that turn also asked the guest a
    real question."""

    def __init__(self, silence_watchdog: SilenceWatchdogProcessor):
        super().__init__()
        self._silence_watchdog = silence_watchdog
        self._watching_for_question = False
        self._buffer: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, FunctionCallsStartedFrame):
            if any(fc.function_name == "end_call" for fc in frame.function_calls):
                self._watching_for_question = True
            await self.push_frame(frame, direction)
            return

        if self._watching_for_question and isinstance(frame, LLMFullResponseStartFrame):
            self._buffer = []
            await self.push_frame(frame, direction)
            return

        if self._watching_for_question and isinstance(frame, LLMTextFrame):
            self._buffer.append(frame.text)
            await self.push_frame(frame, direction)
            return

        if self._watching_for_question and isinstance(frame, LLMFullResponseEndFrame):
            self._watching_for_question = False
            if "?" in "".join(self._buffer):
                logger.warning(
                    "PrematureEndCallGuardProcessor: cancelling the pending hangup -- this turn also "
                    "asked the guest a real question"
                )
                self._silence_watchdog.cancel_end_request()
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
