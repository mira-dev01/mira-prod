"""list[Property] -> RecommendationResult -- reuses Phase 3's
build_property_card so the retrieval pipeline and the guard/pitch-formatter
boundary established in Phase 3 stay the single source of truth for how a
Property becomes guest-facing content.
"""

import dataclasses
from datetime import date

from app.models.property import Property
from app.schemas.tool import RecommendPropertiesArgs
from app.services.property.card import (
    amenity_checklist_note,
    build_property_card,
    comparison_notes,
    match_reasons_for_card,
)
from app.services.property.pitch_formatter import (
    PartiallyAvailableProperty,
    RecommendationResult,
    confidence_for_result,
)


def build_recommendation_result(
    properties: list[Property],
    combo_note: str = "",
    args: RecommendPropertiesArgs | None = None,
    partially_available: list[tuple[Property, list[tuple[date, date]]]] | None = None,
) -> RecommendationResult:
    """partially_available (Availability-first recommendations,
    Implementation 3): properties excluded from `properties` because
    calendar_service classified them "partial" (a real conflict exists in
    the requested window, but the property isn't a clean, no-caveat match --
    see AvailabilityWindowResult's own docstring). Converted here into
    lightweight PartiallyAvailableProperty entries -- never PropertyCards,
    so it's structurally impossible for one to be rendered through
    format_property_pitch_line's normal per-option path as if it were a
    clean recommendation."""
    partial_cards = [
        PartiallyAvailableProperty(
            spoken_name=p.spoken_name or p.display_name or p.raw_name or p.name,
            conflicting_bookings=bookings,
        )
        for p, bookings in (partially_available or [])
    ]
    if not properties:
        # Availability-first recommendations, Implementation 3: a real
        # partial-availability result is meaningfully different from a
        # genuine "nothing matches your criteria at all" -- not_found's
        # existing fixed text ("let me connect you with the host directly")
        # would be actively misleading here, since a property DOES exist,
        # it's just not a clean match for the guest's exact window. Only
        # fall back to not_found when there's truly nothing to say, partial
        # or otherwise.
        return RecommendationResult(options=[], not_found=not partial_cards, partially_available=partial_cards)
    cards = [build_property_card(p) for p in properties]
    # Phase 2.1 (documentation/agent-conversation-improvement.md): args is
    # optional so any other/future caller of build_recommendation_result that
    # doesn't have guest criteria in scope still works, correctly producing
    # no fabricated reasons rather than erroring.
    if args is not None:
        cards = [dataclasses.replace(card, match_reasons=match_reasons_for_card(card, args)) for card in cards]
        # Recommendation conversations ("Phase X"): required_amenities is now
        # a soft ranking preference (filter_builder.apply_amenity_boost), so
        # a returned card can genuinely have some but not all of a guest's
        # accumulated amenity requests -- amenity_checklist_note needs the
        # property's REAL full amenity_tags (never card.top_amenities, which
        # is truncated to 2), so it's computed here from the raw `properties`
        # list, same pattern as unreliable_price_ids below.
        real_amenity_tags_by_id = {p.id: (p.amenity_tags or []) for p in properties}
        cards = [
            dataclasses.replace(
                card,
                amenity_checklist=amenity_checklist_note(
                    args.required_amenities, real_amenity_tags_by_id.get(card.property_id, [])
                ),
            )
            for card in cards
        ]
    # Recommendation engine v2 ("why not that one" / tradeoff reasoning):
    # deliberately skipped when combo_note is set -- that path's cards are
    # smaller units meant to be booked TOGETHER (ranking.diversify_leading_candidates
    # already excludes this same path for the identical reason), so a
    # "this one's ₹X more" comparison would misleadingly frame two
    # complementary units as competing alternatives.
    if not combo_note:
        # exact_airbnb_pricing properties' real price comes from a live
        # SearchApi fetch at get_pricing time, not the stored base_price
        # (which can be stale/a placeholder) -- comparison_notes must never
        # build a spoken price comparison from that unverified number, the
        # same discipline handle_get_pricing/handle_negotiate_rate's own
        # ₹0 guard already applies to this exact base_price column.
        unreliable_price_ids = frozenset(p.id for p in properties if p.exact_airbnb_pricing)
        notes = comparison_notes(cards, unreliable_price_ids)
        cards = [dataclasses.replace(card, comparison_note=notes.get(card.property_id, "")) for card in cards]
    return RecommendationResult(
        options=cards,
        combo_note=combo_note,
        # Phase 2.6: deterministic, from the same real signals (result
        # count, whether the combo/fallback path fired) already computed
        # right here -- never a new judgment call handed to the model.
        recommendation_confidence=confidence_for_result(cards, combo_note),
        partially_available=partial_cards,
    )
