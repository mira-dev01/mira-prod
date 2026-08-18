"""Renders a RecommendationResult (structured PropertyCards) into the
natural-language string actually spoken to a guest.

Kept as a SEPARATE step from handle_recommend_properties's SQL/filtering
logic on purpose: property_recommendation_guard.py used to regex-parse this
rendered text back into structured data to do its job (strip leaked
property_id asides, verify the model actually named a recommended
property). That coupling silently broke if the string format ever changed
without the regex being updated in lockstep. Now the guard is handed the
same RecommendationResult/PropertyCard objects directly -- this module's
output is ONLY for speech, never re-parsed by anything downstream.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from app.services.property.card import PropertyCard

_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}

# Phase 2.6 (documentation/agent-conversation-improvement.md): grounded in
# real, already-computed signals (result count, whether the combo/fallback
# path fired) -- deliberately NOT an LLM self-reported number, which would
# just be another hallucination-shaped output. "strong" = exactly one clean
# match; "moderate" = 2-3 comparable options to choose between; "weak" = the
# combo/fallback path fired (no single property was a full match).
RecommendationConfidence = Literal["strong", "moderate", "weak"]


@dataclass(frozen=True)
class PartiallyAvailableProperty:
    """Availability-first recommendations, Implementation 3: a property that
    is NOT eligible for the main recommended list (a real, guest-visible
    conflict exists somewhere in the requested window -- see
    calendar_service.AvailabilityWindowResult's own docstring for the
    "partial" status definition this represents) but is retained here,
    separately from PropertyCard.options, so the agent has the structured
    data needed to name the actual conflicting dates rather than silently
    dropping the property with no explanation -- matching the task's own
    example phrasing: "There is a booking on this property from October 3rd
    to 5th... once your dates are finalised I can check again." Deliberately
    a SEPARATE, much smaller structure than PropertyCard (not another
    PropertyCard field) -- a partially-available property is never a clean
    recommendation, so it should be structurally impossible to render it
    through format_property_pitch_line's normal per-option path."""

    spoken_name: str
    conflicting_bookings: list[tuple[date, date]] = field(default_factory=list)


@dataclass
class RecommendationResult:
    options: list[PropertyCard]
    combo_note: str = ""
    not_found: bool = False
    recommendation_confidence: RecommendationConfidence = "moderate"
    # Availability-first recommendations, Implementation 3: see
    # PartiallyAvailableProperty's own docstring. Empty on every existing
    # caller/test that doesn't pass check_in/check_out through to
    # orchestrator.recommend_properties -- purely additive, never populated
    # unless a real availability check actually ran and found a partial
    # conflict.
    partially_available: list[PartiallyAvailableProperty] = field(default_factory=list)


_NOT_FOUND_TEXT = "I couldn't find a property in our portfolio matching that -- let me connect you with the host directly."

# Public (not _-prefixed) -- app/voice/response_shape_guard.py (Phase 4.3,
# documentation/agent-conversation-improvement.md) also imports this
# directly, to recognize a recommendation block's fixed intro text when
# checking whether a response contains more than one.
CONFIDENCE_INTROS: dict[RecommendationConfidence, str] = {
    "strong": "This one's a great fit:",
    "moderate": "I have a couple of options that could work well:",
    "weak": "I don't have a single perfect match, but here's what could work if we combine two units:",
}


def confidence_for_result(options: list[PropertyCard], combo_note: str) -> RecommendationConfidence:
    """Deterministic mapping from signals the orchestrator already computes
    -- never a new judgment call handed to the model. combo_note firing
    already means no single property was a full match (sql_search's own
    fallback path), a real lower-confidence signal, not a guess."""
    if combo_note:
        return "weak"
    if len(options) == 1:
        return "strong"
    return "moderate"


def _number_word(n: int) -> str:
    return _NUMBER_WORDS.get(n, str(n))


def _join_natural(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return " and ".join(items)


def format_property_pitch_line(card: PropertyCard, index: int) -> str:
    descriptor_parts = []
    if card.bedroom_count:
        descriptor_parts.append(f"{_number_word(card.bedroom_count)}-bedroom")
    if card.property_type:
        descriptor_parts.append(card.property_type)
    descriptor = " ".join(descriptor_parts) or "property"

    amenity_phrase = f" with {_join_natural(card.top_amenities)}" if card.top_amenities else ""

    # Phase 2.2 (documentation/agent-conversation-improvement.md): ties the
    # recommendation back to why it matches THIS guest, not just what the
    # property is -- e.g. "sleeps 6 -- good for a group of friends who want
    # a private pool." One added clause, not a second sentence (requirement
    # #5's "no information dumping" applies to a good reason just as much as
    # a bad one); card.match_reasons is already capped at 2 by
    # match_reasons_for_card, and empty (producing no clause at all) when no
    # guest criteria were given, per that function's own no-fabrication rule.
    #
    # comparison_note (Recommendation engine v2 -- "why not that one"/tradeoff
    # reasoning) joins into the SAME clause rather than adding a second one --
    # the existing voice-friendly discipline above applies just as much to a
    # comparison as to a match reason. card.comparison_note (set by card.py's
    # comparison_notes function) already caps this to one short fact
    # ("₹1,000 more than Palm Retreat a night"), so joining it alongside
    # match_reasons never produces more than the two-reasons-plus-one-
    # comparison ceiling those two functions already independently enforce.
    clause_parts = list(card.match_reasons)
    if card.comparison_note:
        clause_parts.append(card.comparison_note)
    # amenity_checklist (Recommendation conversations, "Phase X"): required_
    # amenities is a soft ranking preference now, not a hard filter, so a
    # returned property can genuinely have some but not all of a guest's
    # accumulated amenity requests. Per explicit product direction, this
    # must be spoken explicitly (which it has, which it doesn't) so the
    # guest can decide -- joins the same clause, same voice-friendly
    # discipline as match_reasons/comparison_note above, never a second
    # sentence. card.py's amenity_checklist_note already only produces this
    # for a genuinely partial match (2+ requested amenities, neither
    # all-matched nor all-missing), so it's never redundant with the
    # existing single-amenity "has the X you asked for" match_reasons clause.
    if card.amenity_checklist:
        clause_parts.append(card.amenity_checklist)
    reason_clause = f" -- {_join_natural(clause_parts)}" if clause_parts else ""

    return (
        f"{index}. {card.spoken_name}, a {descriptor}{amenity_phrase} in {card.city or 'unlisted city'} "
        f"for ₹{card.base_price:,.0f} a night, sleeps {card.max_guests}{reason_clause}. "
        f"(property_id: {card.property_id})"
    )


def _format_partial_availability_line(entry: PartiallyAvailableProperty) -> str:
    # Availability-first recommendations, Implementation 3: names the actual
    # conflicting dates, matching the task's own example phrasing precisely
    # ("There is a booking on this property from October 3rd to 5th"). Never
    # a generic "not available" -- the whole point of this structure existing
    # separately from a hard exclusion is that the guest gets the real,
    # specific reason instead of the property just silently disappearing.
    conflicts = ", ".join(
        f"{check_in.isoformat()} to {check_out.isoformat()}" for check_in, check_out in entry.conflicting_bookings
    )
    return (
        f"{entry.spoken_name} has a booking from {conflicts} that overlaps part of the requested dates. "
        f"Once the guest's exact dates are finalized, check again -- it may still work, or another "
        f"property can be recommended instead."
    )


def render_recommendation_text(result: RecommendationResult) -> str:
    if result.not_found:
        return _NOT_FOUND_TEXT
    if not result.options:
        # Availability-first recommendations, Implementation 3: zero clean
        # ("full") matches but at least one "partial" one exists -- this is
        # NOT the same as not_found (a property genuinely does exist), so
        # the fixed not_found text ("let me connect you with the host
        # directly") would be misleading here. Speak the partial-availability
        # facts instead of the normal per-option pitch lines.
        if result.partially_available:
            lines = [_format_partial_availability_line(entry) for entry in result.partially_available]
            return "\n".join(lines)
        return _NOT_FOUND_TEXT

    # One property per line, newline-joined -- never " | "-joined. Real
    # property names routinely contain a literal "|" themselves (e.g.
    # imported Airbnb titles like "Azure 1bhk | 5 mins walk to beach |
    # Pause Project"); a newline can never appear inside a single-line DB
    # field, so it can't collide with a name's own delimiters the way " | "
    # did (confirmed live 2026-07-27).
    #
    # The intro line is a cue for the model's *tone*, not a script to read
    # verbatim -- GOLDEN_RULES already instructs it to turn this into a
    # warm, natural pitch rather than reciting a list (see the
    # "conversational warmth" rule in system_prompt.py). Phase 2.6: the
    # exact wording is now driven by recommendation_confidence (a
    # deterministic mapping from real signals, not a new judgment call) --
    # the underlying facts below (names/prices/capacity/reasons) are
    # completely unchanged regardless of which intro is chosen, only the
    # framing language differs.
    intro = CONFIDENCE_INTROS[result.recommendation_confidence]
    lines = [format_property_pitch_line(card, i) for i, card in enumerate(result.options, 1)]
    text = intro + "\n" + "\n".join(lines) + result.combo_note
    # Availability-first recommendations, Implementation 3: at least one full
    # match exists AND at least one partial one -- append the partial facts
    # after the main pitch rather than dropping them, so the agent still has
    # the option to mention "there's also X, but it has a conflicting
    # booking" if the guest asks for more options. Kept structurally separate
    # from the numbered options list (never merged into format_property_pitch_line's
    # own per-option line) so it can't be mistaken for a clean recommendation.
    if result.partially_available:
        partial_lines = [_format_partial_availability_line(entry) for entry in result.partially_available]
        text += "\n" + "\n".join(partial_lines)
    return text
