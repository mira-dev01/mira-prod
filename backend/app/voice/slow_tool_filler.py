"""Speaks a short filler line while a slow tool call is still running, so a
guest isn't left in dead air.

Confirmed live 2026-08-01: recommend_properties (a heavier DB/portfolio
query than the other tools) left a ~30s silent gap between the guest's
question and the spoken recommendation, with nothing telling the guest
Mira was still working on it. get_pricing/check_calendar do not show this
gap on their common path (confirmed same call, ~1.5s tool-call-to-reply),
so this stays scoped to tools with real evidence of crossing a filler
threshold, not every tool call -- a filler on an already-fast call would
just add unnecessary chatter (Phase 3 constraint: no filler for fast
operations).

Phase 3 (documentation, latency/filler investigation): get_pricing and
negotiate_rate are now ALSO scoped tools, but with their OWN higher
threshold (see PRICING_TOOL_DELAY_SECONDS below), not recommend_properties'
1.2s -- their common DB-only path is already close to 1.2s (per the same
confirmed-live measurement above), so reusing that threshold would fire a
filler on nearly every ordinary pricing question. What actually justifies
covering these two tools is exact_airbnb_pricing properties, where
calculate_price falls through to a live SearchApi.io fetch
(app/integrations/searchapi_client.py: up to two sequential HTTP calls,
each a 15s client timeout -- fetch_property_coordinates then
fetch_listing_total_price -- so a worst case near 30s is architecturally
possible, not just theoretical) before falling back to Property.base_price
on any failure. check_calendar and search_faq are deliberately NOT
included -- both are DB-only with no equivalent external-API long-tail
risk (confirmed: no searchapi_client/httpx import anywhere in
calendar_service.py or the search_faq path), so there's no evidence they
need this beyond the same ~1.5s fast-path recommend_properties/get_pricing
already share, and Step 9's own classification discipline says not to add
filler where there's no evidence it's needed.

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
import time
from dataclasses import dataclass

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    FunctionCallCancelFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


@dataclass
class _PendingCall:
    """One concurrently-pending scoped tool call's tracking state -- see
    SlowToolFillerProcessor's own docstring for why this is keyed by
    tool_call_id rather than a single set of fields."""

    function_name: str
    started_at: float
    timer_task: asyncio.Task

# recommend_properties: the original confirmed-live dead-air gap.
# get_pricing/negotiate_rate: Phase 3 addition, see module docstring --
# same SearchApi.io long-tail exposure (calculate_price is shared by both).
# Kept as a set (not individual constants) so another tool can be added
# later without restructuring the processor, same extensibility shape as
# property_recommendation_guard's _ID_LEAK_TOOLS.
DEFAULT_SLOW_TOOLS = {"recommend_properties", "get_pricing", "negotiate_rate"}

DEFAULT_DELAY_SECONDS = 1.2
# Phase 3: get_pricing/negotiate_rate's own common-path latency (~1.5s,
# DB-only) is already close to DEFAULT_DELAY_SECONDS -- reusing that value
# would fire a filler on nearly every ordinary (non-exact_airbnb_pricing)
# pricing question, exactly the "filler on an already-fast call" failure
# mode this module's own docstring already warns against. A higher,
# separate threshold keeps the common fast path silent while still
# covering the SearchApi.io-backed slow path. UNVALIDATED against real
# call logs (no measured baseline exists in this repo for pricing-specific
# timing beyond the one ~1.5s data point already cited above) -- a
# starting point, not a tuned production value, same "starting point, not
# empirically tuned" discipline as Phase 1/2's own unvalidated defaults
# (app/config.py's sarvam_vad_* / max_call_duration_seconds).
PRICING_TOOL_DELAY_SECONDS = 2.0
# Per-tool overrides layered on top of delay_seconds (the default for any
# scoped tool not listed here) -- deliberately NOT a new settings/config
# surface (Step 10's "smallest deterministic mechanism possible"): every
# value here is either the existing proven default or the one new,
# clearly-flagged starting point above, not a large new tuning framework.
DEFAULT_TOOL_DELAY_OVERRIDES = {
    "get_pricing": PRICING_TOOL_DELAY_SECONDS,
    "negotiate_rate": PRICING_TOOL_DELAY_SECONDS,
}

# A few distinct lines, picked in rotation per call -- a human receptionist
# doesn't say the exact same filler every time (same reasoning as
# silence_watchdog's two distinct nudge lines, GOLDEN_RULES' own
# never-repeat-a-sentence rule).
DEFAULT_FILLER_TEXTS = [
    "Let me check that for you.",
    "One moment, just looking that up.",
    "Give me just a second.",
]
# Phase 3: a separate phrase pool for pricing/negotiation specifically, so
# a guest who asks about pricing right after (or right before) a property
# recommendation doesn't hear the exact same line twice in a row -- still
# fixed, deterministic, never LLM-generated, same rotation mechanism as
# DEFAULT_FILLER_TEXTS. English-only (see module docstring's language note
# below) -- no Hindi/Hinglish variant exists here, deliberately; see the
# language-safety note further down in this file for why.
DEFAULT_PRICING_FILLER_TEXTS = [
    "Sure, let me check that for you.",
    "Sure, give me just a moment.",
    "One second, let me look into that.",
]
# Per-tool phrase-pool overrides, same shape/reasoning as
# DEFAULT_TOOL_DELAY_OVERRIDES above.
DEFAULT_TOOL_FILLER_TEXT_OVERRIDES = {
    "get_pricing": DEFAULT_PRICING_FILLER_TEXTS,
    "negotiate_rate": DEFAULT_PRICING_FILLER_TEXTS,
}

# Phase 3 language-safety note (Step 6 of the brief): fillers are part of
# the agent's spoken personality, so ideally a Hindi/Hinglish guest would
# hear a Hindi/Hinglish filler in the same register. This processor does
# NOT currently do that, deliberately: (1) it has no reference to
# ConversationState/ConversationStyle at all today, so it has no signal to
# select on without a real (if small) wiring change; (2) more importantly,
# this repository has NO existing safe, gender-correct Hindi filler text
# anywhere to model new phrases on -- the Phase 0 investigation confirmed
# the female-persona/masculine-Hindi-verb mismatch is entirely unresolved
# and no gender-agreement signal reaches the LLM or any hardcoded string
# path today (see docs/how-it-works.md's Hindi/Persona findings). Writing
# a new hardcoded Hindi filler phrase here risks introducing exactly that
# same confirmed bug in a brand-new code path, for a feature whose whole
# point is sounding natural, not stilted. Per Step 6's own instruction
# ("do not invent a large new language system... report the limitation"),
# this stays English-only in Phase 3. A Hindi/Hinglish guest still hears
# an English filler line during a slow tool call today, same as before
# this phase -- not a regression, just an unclosed gap, explicitly
# reported rather than silently worked around.


class SlowToolFillerProcessor(FrameProcessor):
    """Speaks a short filler line if a scoped tool call is still running
    after a short delay, so slow tool calls don't leave dead air.

    Tracks EVERY concurrently pending scoped call independently (one timer
    per tool_call_id), not just the single most recent one -- confirmed via
    review that a single-pending-call design silently drops filler coverage
    for any call that isn't the LAST scoped tool named in a
    FunctionCallsStartedFrame whose function_calls has more than one entry
    (the LLM can call multiple tools in one turn, e.g. recommend_properties
    + get_pricing together -- FunctionCallsStartedFrame's own docstring:
    "one or more function call execution"). Reproduced directly: two
    scoped tools starting together previously produced exactly one filler,
    for whichever tool happened to be last in the list, even if THAT one
    resolved quickly and the other (never tracked) was the one that was
    actually slow. Phase 3's scope expansion from one tool to three made
    this meaningfully more likely to occur in practice, though the
    single-pending-call shape itself predates Phase 3.

    Phase 3: tracks each pending call's function_name (not just its
    tool_call_id) so a fired timer can look up a per-tool delay/phrase-pool
    override -- e.g. get_pricing/negotiate_rate get a higher threshold and
    a distinct phrase pool from recommend_properties, see module docstring.
    A tool not listed in either override dict simply uses delay_seconds/
    filler_texts, the original, unchanged behavior."""

    def __init__(
        self,
        *,
        slow_tools: set[str] = DEFAULT_SLOW_TOOLS,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
        filler_texts: list[str] = DEFAULT_FILLER_TEXTS,
        tool_delay_overrides: dict[str, float] = DEFAULT_TOOL_DELAY_OVERRIDES,
        tool_filler_text_overrides: dict[str, list[str]] = DEFAULT_TOOL_FILLER_TEXT_OVERRIDES,
    ):
        super().__init__()
        self._slow_tools = slow_tools
        self._delay_seconds = delay_seconds
        self._filler_texts = filler_texts
        self._tool_delay_overrides = tool_delay_overrides
        self._tool_filler_text_overrides = tool_filler_text_overrides

        # Keyed by tool_call_id -- one independent entry (function_name,
        # started_at, timer_task) per concurrently pending scoped call, so
        # two scoped tools starting together each get their own timer and
        # can each independently fire their own filler.
        self._pending: dict[str, _PendingCall] = {}
        # Rotation index is per-phrase-pool (identity-keyed, since the same
        # list object is reused across calls for a given tool/override) so
        # recommend_properties and get_pricing/negotiate_rate each rotate
        # through their own pool independently instead of sharing one
        # index and skipping entries unevenly.
        self._next_filler_index: dict[int, int] = {}

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, FunctionCallsStartedFrame):
            for fc in frame.function_calls:
                if fc.function_name in self._slow_tools:
                    # A prior call with the SAME tool_call_id that errored
                    # out inside the tool handler (llm_service.py's own
                    # run_function_calls catches the exception and pushes an
                    # ErrorFrame instead of ever calling result_callback --
                    # confirmed live: recommend_properties has thrown in
                    # production) never produces a FunctionCallResultFrame or
                    # FunctionCallCancelFrame -- retracking (replacing any
                    # existing entry for this exact tool_call_id) rather than
                    # only checking "is there already an entry" guards
                    # against that stale entry ever blocking tracking, same
                    # reasoning the original single-pending-call code used.
                    # Distinct tool_call_ids (the multi-simultaneous-call
                    # case this dict shape exists for) simply get their own
                    # separate entry, never overwriting each other.
                    logger.debug(
                        "SlowToolFillerProcessor: tool_call_id={} function_name={} started",
                        fc.tool_call_id,
                        fc.function_name,
                    )
                    await self._start_timer(fc.tool_call_id, fc.function_name)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (FunctionCallResultFrame, FunctionCallCancelFrame)):
            pending = self._pending.pop(frame.tool_call_id, None)
            if pending is not None:
                # Phase 3 (Step 13): tool-result timing, paired with the
                # tool-start log above -- together these answer "tool
                # starts -> X ms -> tool result" without a new metrics
                # framework (enable_metrics=True, app/voice/pipeline.py,
                # already covers per-stage TTFB elsewhere; this is the one
                # gap it doesn't cover -- tool execution time specifically).
                elapsed_ms = (time.monotonic() - pending.started_at) * 1000
                logger.debug(
                    "SlowToolFillerProcessor: tool_call_id={} function_name={} finished after {:.0f}ms",
                    frame.tool_call_id,
                    pending.function_name,
                    elapsed_ms,
                )
                await self.cancel_task(pending.timer_task)
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    async def _start_timer(self, tool_call_id: str, function_name: str):
        # Replace (not merge with) any existing entry for this exact
        # tool_call_id -- see the ErrorFrame-retracking reasoning above.
        # Entries for OTHER tool_call_ids are left completely untouched.
        existing = self._pending.pop(tool_call_id, None)
        if existing is not None:
            await self.cancel_task(existing.timer_task)

        delay = self._tool_delay_overrides.get(function_name, self._delay_seconds)
        timer_task = self.create_task(
            self._on_timeout(tool_call_id, function_name, delay), "slow_tool_filler_timer"
        )
        self._pending[tool_call_id] = _PendingCall(
            function_name=function_name, started_at=time.monotonic(), timer_task=timer_task
        )

    async def _on_timeout(self, tool_call_id: str, function_name: str, delay: float):
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        # Clear tracking now, not just let the result handler do it later --
        # the filler has done its one job for THIS tool_call_id. Leaving the
        # entry in place here would mean a call that errors out after this
        # point (no FunctionCallResultFrame/FunctionCallCancelFrame ever
        # arrives for it) blocks nothing for OTHER calls (each has its own
        # entry now), but would leak this one entry forever -- pop it
        # unconditionally once the timer has genuinely fired.
        if self._pending.pop(tool_call_id, None) is None:
            # Already popped by a concurrent result/cancel arriving in the
            # same event-loop tick this timer fired -- do not speak a filler
            # for a call that has, from this processor's point of view,
            # already resolved.
            return

        # Phase 3 (Step 13): "filler threshold reached" / "filler start" --
        # paired with the tool-start/tool-result log lines above, this is
        # the third of the four timestamps Step 13 asks for (the fourth,
        # "actual response begins", is TTFB already logged elsewhere by
        # enable_metrics=True on the tts stage -- see app/voice/pipeline.py
        # -- not duplicated here).
        logger.info(
            "SlowToolFillerProcessor: {} still running after {}s, speaking filler",
            function_name,
            delay,
        )

        filler_texts = self._tool_filler_text_overrides.get(function_name, self._filler_texts)
        pool_key = id(filler_texts)
        index = self._next_filler_index.get(pool_key, 0)
        text = filler_texts[index % len(filler_texts)]
        self._next_filler_index[pool_key] = index + 1
        await self.push_frame(TTSSpeakFrame(text, append_to_context=False))
