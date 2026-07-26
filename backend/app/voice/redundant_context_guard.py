"""Guard against pipecat re-invoking the LLM with an unchanged context.

Root-caused live 2026-07-26 by reading pipecat's own aggregator source
(app/voice/pipeline.py's user_aggregator is a
pipecat.processors.aggregators.llm_response_universal.LLMUserAggregator).
That aggregator defers pushing a new LLMContextFrame if a function-call
result arrives while the bot is still speaking (so several results arriving
in a fast tool-calling chain get bundled into one re-invocation instead of
one per result -- see its _maybe_push_context_after_function_result), and
fires the deferred push on the next BotStoppedSpeakingFrame instead.

Confirmed live: a call went escalate_to_host -> update_lead in one fast
chain while TTS for an earlier line was still playing. The deferred-push
bookkeeping ended up firing a SECOND time after a later, unrelated
BotStoppedSpeakingFrame (once "Anything else I can help you with today?"
finished playing) -- triggering a real LLM completion with literally
nothing new in context since the previous one (no new guest message, no new
tool result). The model, asked to generate something with nothing new to
go on, tended to just wrap up the call (confirmed live: called end_call and
said goodbye immediately, with no guest reply in between).

This is pipecat's own internal bookkeeping, not a bug in this codebase's
tool-calling code, and not safe to patch directly (deep, undocumented
internals -- see pipeline.py's "Call-connect-to-greeting latency" section
for why patching pipecat internals directly caused a prior outage this
session). This processor is a narrow, low-risk application-level guard
instead: sits between user_aggregator and llm, tracks the message count of
the last LLMContextFrame actually forwarded to the LLM, and drops (does not
forward) any subsequent LLMContextFrame whose context has the same or fewer
messages -- there's nothing new for the LLM to respond to, so don't ask it
to.
"""

from loguru import logger

from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class RedundantContextGuardProcessor(FrameProcessor):
    """Drops an LLMContextFrame if nothing was added to context since the
    last one actually forwarded to the LLM."""

    def __init__(self):
        super().__init__()
        self._last_forwarded_message_count = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMContextFrame):
            message_count = len(frame.context.messages)
            if message_count <= self._last_forwarded_message_count:
                logger.warning(
                    "RedundantContextGuardProcessor: dropping an LLM re-invocation with no "
                    "new context (message_count={}, same as last forwarded) -- likely "
                    "pipecat's own deferred context-push firing with nothing new to respond to",
                    message_count,
                )
                return
            self._last_forwarded_message_count = message_count

        await self.push_frame(frame, direction)
