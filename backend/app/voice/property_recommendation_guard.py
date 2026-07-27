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
   answer). Scoped to recommend_properties only, since it's the one tool with cleanly
   parseable "Name in City: ..." result lines this guard can build a
   guaranteed-correct fallback reply from.

Sits between llm and tts (same position as the other voice guards). Buffers only the
one LLM response immediately following an armed tool call -- every other turn passes
straight through unbuffered, no added latency to the common case.

record_tool_result() is called directly from app/voice/tools.py's recommend_properties
wrapper, synchronously, BEFORE it calls params.result_callback(result) -- not racy the
way escalation_phrase_guard's old arm() was: that race was between an out-of-band call
and text from the SAME completion already in flight. Here, the next completion is
CAUSED by result_callback, so recording the parsed options before calling it guarantees
they're set before any frame from the resulting completion can reach this processor.
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

# Matches handle_recommend_properties's own result-line format (tool_handlers.py):
# "{name} in {city}: ₹{price}/night, sleeps {guests}, ..."
_RECOMMEND_LINE_RE = re.compile(r"^(?P<name>.+?) in (?P<city>[^:]+): ₹(?P<price>[\d,]+)/night, sleeps (?P<guests>\d+)")


def strip_property_ids(text: str) -> str:
    text = _PROPERTY_ID_ASIDE_RE.sub("", text)
    text = _UUID_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def parse_recommend_properties_result(result) -> list[dict]:
    """Best-effort parse of handle_recommend_properties's result string into
    structured options. Returns [] for anything that doesn't match the expected
    shape (e.g. the "I couldn't find a property..." not-found message) --
    callers treat an empty list as "nothing to verify/fall back to"."""
    if not isinstance(result, str) or not result.startswith("Here are some options:"):
        return []
    body = result[len("Here are some options:") :].split(" | ")
    options = []
    for segment in body:
        match = _RECOMMEND_LINE_RE.match(segment.strip())
        if match:
            options.append(match.groupdict())
    return options


def _fallback_recommendation_text(options: list[dict]) -> str:
    lines = [f"{o['name']} at {o['price']} rupees per night, sleeping {o['guests']}" for o in options]
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
        if function_name == "recommend_properties":
            self._pending_options = parse_recommend_properties_result(result)

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
