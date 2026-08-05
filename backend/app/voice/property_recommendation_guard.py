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

Phase 4b (documentation/agent-conversation-improvement.md), "Tool Output Fidelity": the
same pattern generalized beyond recommend_properties. Reframing on review: C1
(recommendation violated a stated guest-count) and C2 (₹0 spoken as "free of charge")
are both symptoms of one broader class -- the model is free to reinterpret a tool's
structured result before speaking it. Phase 1.4/2.0 already fix the *data* those two
specific bugs came from; this is the general-purpose backstop so a future misstatement
of a correct number (price, capacity) still gets caught. record_tool_result now also
accepts a PriceQuoteFact (get_pricing/negotiate_rate) -- the real total the tool
computed -- and verifies the model's reply states that same number before it reaches
TTS, exactly mirroring the existing recommend_properties name-check below. Capacity
fidelity (4b.2) reuses the SAME _pending_options recorded for recommend_properties: if
the reply states a guest-count figure for a named property that doesn't match that
property's own real max_guests, that's a mechanically-checkable violation the same way
a missing property name already is. check_calendar gets a lighter boolean
available/not-available contradiction check (its verifiable fact isn't a number).
search_faq (4b.3, lowest priority) gets the coarsest check of the four: the reply must
not name a specific, recognized amenity keyword (from the same fixed synonym map
recommend_properties' own canonicalization already uses,
app/services/amenity_taxonomy.py) that's genuinely absent from the property's real
amenities list -- a free-text paraphrase of a real fact is never flagged, only a
keyword for an amenity that was never on file at all.
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

from app.services.amenity_taxonomy import _AMENITY_SYNONYMS, canonicalize_amenity
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

# Tools whose result carries one real, verifiable total the model must relay
# faithfully -- Phase 4b.1. Populated via record_tool_result's price_fact param.
_PRICE_FIDELITY_TOOLS = {"get_pricing", "negotiate_rate"}

# check_calendar's verifiable fact is a boolean (available/not), not a
# number -- a separate, lighter check from the price ones above. Only fires
# when the reply asserts the OPPOSITE of what the tool actually returned;
# a reply that doesn't clearly assert either way (e.g. just relays dates
# without the words below) is left alone rather than guessed at.
_AVAILABLE_ASSERTION_RE = re.compile(r"\b(is\s+available|are\s+available)\b", re.IGNORECASE)
_UNAVAILABLE_ASSERTION_RE = re.compile(
    r"\b(not\s+available|isn'?t\s+available|aren'?t\s+available|unavailable|already\s+booked)\b", re.IGNORECASE
)

# A number immediately followed by "rupees"/"rs"/"inr" (any of the ways the LLM
# tends to render ₹ amounts in speech, per real transcripts) or preceded by ₹.
# Deliberately permissive on formatting (commas, decimals) since it only needs
# to extract candidate numbers to compare against one known-correct total, not
# validate the text's grammar.
_SPOKEN_AMOUNT_RE = re.compile(
    r"₹\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:rupees|rs\.?|inr)\b", re.IGNORECASE
)


def _extract_amounts(text: str) -> list[float]:
    amounts = []
    for m in _SPOKEN_AMOUNT_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        try:
            amounts.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return amounts


def _amount_present(text: str, expected: float, tolerance: float = 1.0) -> bool:
    """tolerance=1.0 (not exact float equality) absorbs harmless rounding in
    how the model renders a number in words vs. the tool's own float total."""
    return any(abs(amount - expected) <= tolerance for amount in _extract_amounts(text))


# A guest-count mention worth checking against a named property's real
# max_guests -- "sleeps N"/"sleeps up to N" is the exact phrasing
# format_property_pitch_line (pitch_formatter.py) itself generates, so this
# catches the model both faithfully relaying that phrase and rephrasing it
# with a wrong number.
_SLEEPS_N_RE = re.compile(r"sleeps?\s+(?:up to\s+)?(\d+)", re.IGNORECASE)

# Phase 4b.3: the lower-priority tail of Tool Output Fidelity -- search_faq's
# returned text already includes every real amenity on file
# (faq_service.full_property_context's "Amenities: ..." line, fed from the
# SAME Property.amenities the guard is handed here). Deliberately a coarser
# check than the numeric ones above, per the plan's own scoping: FAQ answers
# are free text, not a single verifiable number, so this only confirms the
# reply doesn't assert a *specific, recognizable amenity keyword* (from the
# same fixed synonym map recommend_properties' own canonicalization already
# uses -- app/services/amenity_taxonomy.py) that's genuinely absent from the
# real list, not a full semantic-equivalence check of the whole reply.
_AMENITY_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_AMENITY_SYNONYMS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

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
    intro = "I found a couple of options I think could work well: " if len(options) > 1 else "I found one that could work well: "
    return intro + "; ".join(lines) + ". Which one sounds interesting?"


def _fallback_price_text(property_name: str, total: float) -> str:
    return f"That comes to ₹{total:,.0f} total for {property_name}."


def _fallback_availability_text(property_name: str, available: bool) -> str:
    return f"{property_name} is {'available' if available else 'not available'} for those dates."


_FAQ_FIDELITY_FALLBACK = (
    "I don't have verified information about that on file. Let me check with the host so you get the "
    "correct details."
)


class PropertyRecommendationGuardProcessor(FrameProcessor):
    """Strips leaked property IDs and backstops a skipped recommend_properties
    readout, for the one LLM response immediately after an armed tool call.

    Phase 4b (documentation/agent-conversation-improvement.md) also backstops
    two Tool Output Fidelity checks for that same one response: the model
    stating a different total than get_pricing/negotiate_rate actually
    computed (price fidelity, 4b.1), and the model stating a guest-count
    figure for a named property that doesn't match its real max_guests
    (capacity fidelity, 4b.2) -- the identical "verify against the tool's own
    real data, override with a guaranteed-correct line if it doesn't match"
    pattern already proven for recommend_properties' name check below.
    """

    def __init__(self):
        super().__init__()
        self._armed_tool: str | None = None
        self._pending_options: list[dict] = []
        self._pending_price_fact: dict | None = None
        self._pending_availability_fact: dict | None = None
        self._pending_faq_fact: dict | None = None
        self._buffering = False
        self._buffer: list[str] = []

    def record_tool_result(self, function_name: str, result) -> None:
        if function_name == "recommend_properties" and isinstance(result, RecommendationResult):
            self._pending_options = [
                {"name": card.spoken_name, "price": card.base_price, "guests": card.max_guests}
                for card in result.options
            ]
        elif function_name in _PRICE_FIDELITY_TOOLS and isinstance(result, dict):
            # Deliberately a plain dict, not a new dataclass shared with
            # pricing_engine -- the guard only ever needs a property name and
            # one real total, and app/voice/tools.py's wrappers already have
            # both readily at hand (the same values app/voice/tools.py's
            # get_pricing wrapper already threads into
            # ConversationState.record_quoted_price, Phase 4.1) without
            # needing to import PriceBreakdown/NegotiationResult here.
            if "property_name" in result and "total" in result:
                self._pending_price_fact = {
                    "property_name": result["property_name"],
                    "total": result["total"],
                }
        elif function_name == "check_calendar" and isinstance(result, dict):
            if "property_name" in result and "available" in result:
                self._pending_availability_fact = {
                    "property_name": result["property_name"],
                    "available": result["available"],
                }
        elif function_name == "search_faq" and isinstance(result, dict):
            if "amenities" in result:
                self._pending_faq_fact = {
                    "real_canonical_amenities": {canonicalize_amenity(a) for a in result["amenities"]}
                }

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
            price_fact = self._pending_price_fact
            availability_fact = self._pending_availability_fact
            faq_fact = self._pending_faq_fact
            self._armed_tool = None
            self._pending_options = []
            self._pending_price_fact = None
            self._pending_availability_fact = None
            self._pending_faq_fact = None

            if not text.strip():
                await self.push_frame(frame, direction)
                return

            stripped = strip_property_ids(text)
            if stripped != text:
                logger.warning("PropertyRecommendationGuardProcessor: stripped a leaked property_id from the reply")
            text = stripped

            if armed_tool in _PRICE_FIDELITY_TOOLS and price_fact:
                if not _amount_present(text, price_fact["total"]):
                    logger.warning(
                        "PropertyRecommendationGuardProcessor: overriding reply -- did not state the "
                        "actual quoted total for {}",
                        price_fact["property_name"],
                    )
                    text = _fallback_price_text(price_fact["property_name"], price_fact["total"])

            if armed_tool == "check_calendar" and availability_fact:
                asserts_available = bool(_AVAILABLE_ASSERTION_RE.search(text))
                asserts_unavailable = bool(_UNAVAILABLE_ASSERTION_RE.search(text))
                real_available = availability_fact["available"]
                # Only correct a CLEAR, unambiguous flip -- the reply
                # asserting the opposite of what the tool actually returned.
                # A reply that asserts neither (doesn't use either phrasing
                # at all) or somehow asserts both is left alone rather than
                # guessed at -- this check exists to catch a clear
                # contradiction, not to police phrasing choices.
                contradicts = (real_available and asserts_unavailable and not asserts_available) or (
                    not real_available and asserts_available and not asserts_unavailable
                )
                if contradicts:
                    logger.warning(
                        "PropertyRecommendationGuardProcessor: overriding reply -- contradicted the "
                        "actual availability ({}) for {}",
                        real_available,
                        availability_fact["property_name"],
                    )
                    text = _fallback_availability_text(availability_fact["property_name"], real_available)

            if armed_tool == "search_faq" and faq_fact is not None:
                real_amenities = faq_fact["real_canonical_amenities"]
                mentioned = {canonicalize_amenity(m) for m in _AMENITY_KEYWORD_RE.findall(text)}
                invented = mentioned - real_amenities
                if invented:
                    logger.warning(
                        "PropertyRecommendationGuardProcessor: overriding reply -- named amenity keyword(s) "
                        "not actually on file: {}",
                        invented,
                    )
                    text = _FAQ_FIDELITY_FALLBACK

            if armed_tool == "recommend_properties" and options:
                # Capacity fidelity (4b.2): a named property whose stated
                # guest-count doesn't match its real max_guests is corrected
                # the same way a missing name already triggers the fallback
                # below -- checked BEFORE the name check so a capacity
                # violation on an otherwise-correctly-named reply still gets
                # caught (the name check alone would let it straight through).
                # Only checks a "sleeps N" mention immediately following (in
                # the same sentence as) the property's own name -- with
                # MULTIPLE options each correctly named alongside their own
                # correct capacity, a global cross-product of every name
                # against every number in the whole text would false-positive
                # on option A's name coexisting with option B's real (and
                # equally real) different capacity elsewhere in the text.
                capacity_violation = False
                for o in options:
                    name_lower = o["name"].lower()
                    idx = text.lower().find(name_lower)
                    if idx == -1:
                        continue
                    # Look only in the same sentence: from the name onward,
                    # up to the next terminal punctuation.
                    tail = text[idx : idx + 200]
                    sentence_end = min((p for p in (tail.find(c) for c in ".!?") if p != -1), default=len(tail))
                    sentence = tail[:sentence_end]
                    match = _SLEEPS_N_RE.search(sentence)
                    if match and int(match.group(1)) != o["guests"]:
                        capacity_violation = True
                        break
                if capacity_violation:
                    logger.warning(
                        "PropertyRecommendationGuardProcessor: overriding reply -- stated guest-count "
                        "didn't match a named property's real max_guests"
                    )
                    text = _fallback_recommendation_text(options)

                named = any(o["name"].lower() in text.lower() for o in options)
                if not named:
                    logger.warning(
                        "PropertyRecommendationGuardProcessor: overriding reply -- did not name any of "
                        "the properties recommend_properties actually returned"
                    )
                    text = _fallback_recommendation_text(options)

            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame(text))
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
