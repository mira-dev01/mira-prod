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

from app.services.property.budget import BudgetConstraint
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
    # Explicit budget semantics (app/services/property/budget.py): the
    # normalized budget this search actually applied (None = no budget), and
    # properties within NEAR_BUDGET_STRETCH of it but NOT within it -- kept
    # out of `options` so the guard/state never treat them as matches.
    budget: BudgetConstraint | None = None
    near_budget: list[PropertyCard] = field(default_factory=list)
    # One better-but-pricier property (premium, or more of what the guest
    # asked for) pitched after the options as "a bit steeper" -- see
    # orchestrator._pick_upsell. Like near_budget, never part of `options`.
    upsell: PropertyCard | None = None
    upsell_reason: str = ""
    # True only when the search itself errored (never for zero matches) --
    # see recommendation_failed_result. An empty search and a backend failure
    # must never render the same, or a crash becomes "nothing fits your
    # budget" (confirmed live 2026-09-25).
    failed: bool = False


def recommendation_failed_result() -> RecommendationResult:
    return RecommendationResult(options=[], failed=True)


# Success vs failure itself is carried by app/voice/tool_contract.py's
# result envelope ("success"/"status"); this text is the failure message.
RECOMMENDATION_FAILED_TEXT = (
    "The property search did not run (a system error, NOT an empty result). You have no property options "
    "from this call: do not name, describe, or price any property, and do not say nothing matched. Tell the "
    "guest you couldn't pull up the options just now and offer to have the host get back to them."
)

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


def _stay_price_clause(card: PropertyCard) -> str:
    """The guest's actual stay total, pre-computed (the model never
    multiplies), plus the host's automatic length-of-stay offer when one
    applies -- stated as a concrete offer so it can be pitched with
    conviction. Empty when the stay length isn't known."""
    sp = card.stay_price
    if sp is None or sp.total is None or not sp.nights:
        return ""
    nights = f"{sp.nights} night{'s' if sp.nights != 1 else ''}"
    if sp.offer_percent:
        clause = (
            f" -- with the host's {sp.offer_min_nights}+ night offer ({sp.offer_percent:.0f}% off) your {nights} "
            f"come to ₹{sp.total:,.0f} instead of ₹{sp.standard_total:,.0f}"
        )
    elif sp.nights > 1:
        clause = f" (₹{sp.total:,.0f} for your {nights})"
    else:
        clause = ""
    if sp.is_estimate:
        clause += " -- an estimate until the exact dates are priced"
    return clause


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
        f"for ₹{card.nightly_rate:,.0f} a night{_stay_price_clause(card)}, sleeps {card.max_guests}{reason_clause}. "
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


def _rupees(amount: float) -> str:
    return f"₹{amount:,.0f}"


def _budget_phrase(budget: BudgetConstraint) -> str:
    """The applied budget in the guest's own basis, with the other basis
    pre-computed alongside it -- the model relays these numbers, it never
    derives one from the other itself."""
    if budget.basis == "total_stay":
        nightly = _rupees(budget.max_nightly_rate)
        return f"{_rupees(budget.amount)} total for {budget.nights} nights (about {nightly} a night)"
    phrase = f"{_rupees(budget.amount)} per night"
    if budget.max_total_rate is not None and budget.nights and budget.nights > 1:
        phrase += f" ({_rupees(budget.max_total_rate)} for {budget.nights} nights)"
    if budget.basis_assumed:
        phrase += " -- assumed per night since the guest didn't say; don't ask about it again"
    return phrase


def _clarification_text(budget: BudgetConstraint) -> str:
    amount = _rupees(budget.amount)
    if budget.clarification == "budget_basis":
        question = f"is that {amount} per night, or {amount} for the entire stay?"
    elif budget.clarification == "stay_length" and budget.basis == "total_stay":
        question = f"how many nights is the {amount} total budget for?"
    elif budget.clarification == "stay_length":
        question = "how many nights they're planning to stay?"
    else:
        question = "what their budget is in rupees?"
    return (
        f"Not searched yet -- one detail is needed first. Ask the guest one short question: {question} "
        "Then call recommend_properties again with their answer. Do not name or suggest any property until then."
    )


def _format_near_budget_line(card: PropertyCard, budget: BudgetConstraint) -> str:
    sp = card.stay_price
    total = sp.total if sp is not None else None
    over = _rupees(budget.over_budget_by(card.applicable_nightly_rate, total))
    if budget.basis == "total_stay" and total is not None:
        price = f"{_rupees(total)} for {sp.nights} nights, {over} over their total budget"
    else:
        price = f"{_rupees(card.applicable_nightly_rate)} a night, {over} a night over their budget"
    return (
        f"Slightly ABOVE the guest's budget (only mention as a stretch option, never as within budget): "
        f"{card.spoken_name} in {card.city or 'unlisted city'} at {price}, sleeps {card.max_guests}. "
        f"(property_id: {card.property_id})"
    )


def _format_upsell_line(card: PropertyCard, reason: str, budget: BudgetConstraint) -> str:
    """Per product direction (Mira as a sales agent): the better option is
    pitched in the SAME turn as the in-budget ones, with its real price and
    the host's offer stated up front -- the offer is the one discount for
    it, never held back for a later negotiation."""
    sp = card.stay_price
    total = sp.total if sp is not None else None
    over = _rupees(budget.over_budget_by(card.applicable_nightly_rate, total))
    over_phrase = f"{over} over their total budget" if budget.basis == "total_stay" and total is not None else (
        f"{over} a night over their budget"
    )
    offer = (
        " Lead with the offer -- it's already the best price on it."
        if sp is not None and sp.offer_percent
        else ""
    )
    return (
        "STEEPER OPTION (above budget -- after the options above and before your one closing question, tell the "
        "guest you have another option that's a bit steeper, give its price, and say why it's worth it; never "
        "present it as within budget):"
        f"{offer}\n{card.spoken_name} in {card.city or 'unlisted city'} -- {reason} -- for "
        f"₹{card.nightly_rate:,.0f} a night{_stay_price_clause(card)}, sleeps {card.max_guests}; {over_phrase}. "
        f"(property_id: {card.property_id})"
    )


def _offer_line(result: RecommendationResult) -> str:
    """Host-configured automatic length-of-stay offers on the recommended
    options are a real selling point -- per product direction, pitch them
    with conviction rather than holding them back like a negotiation
    discount (get_pricing/negotiate_rate then quote from the offer price for
    these properties, so the guest never hears a higher number later)."""
    names = [c.spoken_name for c in result.options if c.stay_price is not None and c.stay_price.offer_percent]
    if not names:
        return ""
    fits = " and that it brings the stay within their budget" if result.budget is not None else ""
    return (
        f"\nOFFER: {', '.join(names)} {'has' if len(names) == 1 else 'have'} an automatic longer-stay offer for "
        f"this stay. Pitch it confidently as a real offer{fits}, and end with one question (e.g. would they like "
        "the details)."
    )


def render_recommendation_text(result: RecommendationResult) -> str:
    if result.failed:
        return RECOMMENDATION_FAILED_TEXT
    budget = result.budget
    if budget is not None and budget.clarification is not None:
        return _clarification_text(budget)
    budget_line = f"\nBudget applied: {_budget_phrase(budget)}." if budget is not None else ""
    near_lines = (
        [_format_near_budget_line(card, budget) for card in result.near_budget] if budget is not None else []
    )
    if budget is not None and result.upsell is not None:
        near_lines.append(_format_upsell_line(result.upsell, result.upsell_reason, budget))
    if result.not_found:
        if budget is not None:
            return (
                f"No property matches these criteria within that budget.{budget_line}\nSay so plainly and offer "
                "to widen the budget or other criteria; if they'd rather not, offer to connect them with the host."
            )
        return _NOT_FOUND_TEXT
    if not result.options:
        # Availability-first recommendations, Implementation 3: zero clean
        # ("full") matches but at least one "partial" one exists -- this is
        # NOT the same as not_found (a property genuinely does exist), so
        # the fixed not_found text ("let me connect you with the host
        # directly") would be misleading here. Speak the partial-availability
        # facts instead of the normal per-option pitch lines.
        lines = [_format_partial_availability_line(entry) for entry in result.partially_available]
        if not lines and not near_lines:
            return _NOT_FOUND_TEXT
        if near_lines:
            lines = [
                "Nothing is within the guest's budget -- say that first, then offer these as stretch options.",
                *near_lines,
                *lines,
            ]
        return "\n".join(lines) + budget_line

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
    # combo_note belongs to the options themselves ("book two of these
    # together"), so it's appended right after them, never after a
    # near-budget/steeper line.
    text = intro + "\n" + "\n".join(lines) + result.combo_note
    if near_lines:
        text += "\n" + "\n".join(near_lines)
    text += budget_line + _offer_line(result)
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
