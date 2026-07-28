"""Code-level backstop for two confirmed-live recommend_properties failures:

1. Property IDs (raw UUIDs) getting spoken to the guest. handle_recommend_properties
   (app/services/tool_handlers.py) embeds "(property_id: <uuid>)" in its tool-result
   text so the model can carry the ID forward for later tool calls (get_pricing,
   check_calendar, send_photos, ...) -- but the model sometimes echoes that literally
   instead of treating it as data. Confirmed live 2026-07-27: "(property ID
   48c687d2-7be8-435c-951c-080d5bab0314)" appeared verbatim in a guest-facing reply.
   property_id also appears in the portfolio listing embedded in the system prompt
   every turn (system_prompt.py) and in other property-aware tool results (get_pricing,
   check_calendar, negotiate_rate, search_faq, send_photos, dispatch_technician) -- so
   this guard arms on any of those, not just recommend_properties.

2. Calling recommend_properties and then not actually naming any of the returned
   properties in the next reply. GOLDEN_RULES already bans this (system_prompt.py's
   "Never react to a tool result before you've actually said what's in it"),
   confirmed live as a recurring failure prompting alone doesn't reliably prevent
   (guest had to say "You didn't recommend any properties" before getting a real
   answer). Scoped to recommend_properties only.

Sits between llm and tts (same position as the other voice guards). Buffers only the
one LLM response immediately following an armed tool call -- every other turn passes
straight through unbuffered, no added latency to the common case.

record_tool_result() is called directly from app/voice/tools.py's recommend_properties
wrapper, synchronously, BEFORE it calls params.result_callback(rendered_result) -- not
racy the way escalation_phrase_guard's old arm() was: that race was between an
out-of-band call and text from the SAME completion already in flight. Here, the next
completion is CAUSED by result_callback, so recording the options before calling it
guarantees they're set before any frame from the resulting completion can reach this
processor.

Previously this guard regex-parsed handle_recommend_properties's RENDERED text back
into structured data (name/city/price/guests) to do its job -- a coupling that
silently broke any time the rendered pitch string's format changed without the regex
being updated in lockstep (see git history: a real name containing a literal "|" once
tore itself apart this way). handle_recommend_properties now returns a structured
RecommendationResult (app/services/property/pitch_formatter.py) directly, and
app/voice/tools.py hands THAT to record_tool_result -- this guard never touches or
parses rendered speech text for its own bookkeeping, only for the strip/verify pass on
the model's actual reply below. Any future change to the pitch string's wording has
zero effect on this file.
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

from app.services.property.pitch_formatter import RecommendationResult

# Any tool whose result (or the system-prompt portfolio listing feeding it) can
# carry a property_id into context -- arms the UUID-stripping half of this guard.
_ID_LEAK_TOOLS = {
    "recommend_properties",
    "get_pricing",
    "check_calendar",
    "negotiate_rate",
    "search_faq",
    "send_photos",
    "dispatch_technician",
}

_UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_UUID_RE = re.compile(_UUID_PATTERN, re.IGNORECASE)
# Strips the whole "(property_id: <uuid>)" / "(property ID <uuid>)" aside, not just
# the UUID -- leaves a clean sentence instead of a dangling empty "()".
_PROPERTY_ID_ASIDE_RE = re.compile(r"\(\s*property[\s_]?id:?\s*" + _UUID_PATTERN + r"\s*\)", re.IGNORECASE)


def strip_property_ids(text: str) -> str:
    text = _PROPERTY_ID_ASIDE_RE.sub("", text)
    text = _UUID_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _fallback_recommendation_text(options: list[dict]) -> str:
    lines = [f"{o['name']} at {o['price']:,.0f} rupees per night, sleeping {o['guests']}" for o in options]
    return "Here are some options: " + "; ".join(lines) + ". Which one sounds interesting?"


class PropertyRecommendationGuardProcessor(FrameProcessor):
    """Strips leaked property IDs and backstops a skipped recommend_properties
    readout, for the one LLM response immediately after an armed tool call."""

    def __init__(self):
        super().__init__()
        self._armed_tool: str | None = None
        self._pending_options: list[dict] = []
        self._buffering = False
        self._buffer: list[str] = []

    def record_tool_result(self, function_name: str, result) -> None:
        if function_name == "recommend_properties" and isinstance(result, RecommendationResult):
            self._pending_options = [
                {"name": card.spoken_name, "price": card.base_price, "guests": card.max_guests}
                for card in result.options
            ]

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, FunctionCallsStartedFrame):
            for fc in frame.function_calls:
                if fc.function_name in _ID_LEAK_TOOLS:
                    self._armed_tool = fc.function_name
            await self.push_frame(frame, direction)
            return

        if not self._armed_tool:
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

            armed_tool = self._armed_tool
            options = self._pending_options
            self._armed_tool = None
            self._pending_options = []

            if not text.strip():
                await self.push_frame(frame, direction)
                return

            text = strip_property_ids(text)

            if armed_tool == "recommend_properties" and options:
                named = any(o["name"].lower() in text.lower() for o in options)
                if not named:
                    text = _fallback_recommendation_text(options)

            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame(text))
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
