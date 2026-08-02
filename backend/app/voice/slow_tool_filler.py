"""Speaks a short filler line while a slow tool call is still running, so a
guest isn't left in dead air.

Confirmed live 2026-08-01: recommend_properties (a heavier DB/portfolio
query than the other tools) left a ~30s silent gap between the guest's
question and the spoken recommendation, with nothing telling the guest
Mira was still working on it. get_pricing/check_calendar do not show this
gap (confirmed same call, ~1.5s tool-call-to-reply) so this is scoped
narrowly to the tools that are actually slow, not every tool call -- a
filler on an already-fast call would just add unnecessary chatter.

Sits right after llm (same position as the other voice guards -- see
app/voice/pipeline.py) so it sees FunctionCallsStartedFrame the moment a
scoped tool is dispatched, same hook property_recommendation_guard uses to
arm itself.

Design: a delayed one-shot timer, not an immediate filler. Most tool calls
of any kind resolve in well under a second; firing a filler unconditionally
the instant the tool starts would talk over a reply that was about to
arrive anyway. The delay is cancelled the moment a FunctionCallResultFrame
or FunctionCallCancelFrame for the same tool_call_id arrives, so a call
that happens to resolve quickly never hears the filler at all -- same
timer-cancel-on-real-signal shape as silence_watchdog's own nudge timer.

Uses append_to_context=False (same as silence_watchdog's nudges and the
call greeting) -- a filler is not part of the real conversation and should
never appear in the LLM's own context on the next turn.
"""

import asyncio

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    FunctionCallCancelFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Only recommend_properties has a confirmed-live dead-air gap. Kept as a set
# (not a single constant) so another tool can be added here later without
# restructuring the processor, same extensibility shape as
# property_recommendation_guard's _ID_LEAK_TOOLS.
DEFAULT_SLOW_TOOLS = {"recommend_properties"}

DEFAULT_DELAY_SECONDS = 1.2
# A few distinct lines, picked in rotation per call -- a human receptionist
# doesn't say the exact same filler every time (same reasoning as
# silence_watchdog's two distinct nudge lines, GOLDEN_RULES' own
# never-repeat-a-sentence rule).
DEFAULT_FILLER_TEXTS = [
    "Let me check that for you.",
    "One moment, just looking that up.",
    "Give me just a second.",
]


class SlowToolFillerProcessor(FrameProcessor):
    """Speaks a short filler line if a scoped tool call is still running
    after a short delay, so slow tool calls don't leave dead air."""

    def __init__(
        self,
        *,
        slow_tools: set[str] = DEFAULT_SLOW_TOOLS,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
        filler_texts: list[str] = DEFAULT_FILLER_TEXTS,
    ):
        super().__init__()
        self._slow_tools = slow_tools
        self._delay_seconds = delay_seconds
        self._filler_texts = filler_texts

        self._pending_tool_call_id: str | None = None
        self._timer_task: asyncio.Task | None = None
        self._next_filler_index = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, FunctionCallsStartedFrame):
            for fc in frame.function_calls:
                if fc.function_name in self._slow_tools:
                    # Always retrack onto the newest scoped call, even if one
                    # was already pending. A prior call that errored out
                    # inside the tool handler (llm_service.py's own
                    # run_function_calls catches the exception and pushes an
                    # ErrorFrame instead of ever calling result_callback --
                    # confirmed live: recommend_properties has thrown in
                    # production) never produces a FunctionCallResultFrame or
                    # FunctionCallCancelFrame, so _pending_tool_call_id would
                    # otherwise stay stuck on that dead call forever and
                    # silently block every later filler for the rest of the
                    # call session. Retracking here is a correctness fix, not
                    # just a convenience -- old behavior only checked "is
                    # None".
                    self._pending_tool_call_id = fc.tool_call_id
                    await self._start_timer()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (FunctionCallResultFrame, FunctionCallCancelFrame)):
            if frame.tool_call_id == self._pending_tool_call_id:
                self._pending_tool_call_id = None
                await self._cancel_timer()
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    async def _start_timer(self):
        await self._cancel_timer()
        self._timer_task = self.create_task(self._on_timeout(), "slow_tool_filler_timer")

    async def _cancel_timer(self):
        if self._timer_task:
            await self.cancel_task(self._timer_task)
            self._timer_task = None

    async def _on_timeout(self):
        try:
            await asyncio.sleep(self._delay_seconds)
        except asyncio.CancelledError:
            return
        finally:
            self._timer_task = None

        if self._pending_tool_call_id is None:
            return
        # Clear tracking now, not just cancel the timer -- the filler has
        # done its one job for this call. Leaving the id set here would mean
        # a call that errors out after this point (no FunctionCallResultFrame
        # or FunctionCallCancelFrame ever arrives) blocks tracking for every
        # later scoped call in the same session, same failure this guards
        # against in FunctionCallsStartedFrame above.
        self._pending_tool_call_id = None

        text = self._filler_texts[self._next_filler_index % len(self._filler_texts)]
        self._next_filler_index += 1
        logger.info("SlowToolFillerProcessor: tool call still running after {}s, speaking filler", self._delay_seconds)
        await self.push_frame(TTSSpeakFrame(text, append_to_context=False))
