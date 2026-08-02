"""list[Property] -> RecommendationResult -- reuses Phase 3's
build_property_card so the retrieval pipeline and the guard/pitch-formatter
boundary established in Phase 3 stay the single source of truth for how a
Property becomes guest-facing content.
"""

import dataclasses

from app.models.property import Property
from app.schemas.tool import RecommendPropertiesArgs
from app.services.property.card import build_property_card, match_reasons_for_card
from app.services.property.pitch_formatter import RecommendationResult, confidence_for_result


def build_recommendation_result(
    properties: list[Property], combo_note: str = "", args: RecommendPropertiesArgs | None = None
) -> RecommendationResult:
    if not properties:
        return RecommendationResult(options=[], not_found=True)
    cards = [build_property_card(p) for p in properties]
    # Phase 2.1 (documentation/agent-conversation-improvement.md): args is
    # optional so any other/future caller of build_recommendation_result that
    # doesn't have guest criteria in scope still works, correctly producing
    # no fabricated reasons rather than erroring.
    if args is not None:
        cards = [dataclasses.replace(card, match_reasons=match_reasons_for_card(card, args)) for card in cards]
    return RecommendationResult(
        options=cards,
        combo_note=combo_note,
        # Phase 2.6: deterministic, from the same real signals (result
        # count, whether the combo/fallback path fired) already computed
        # right here -- never a new judgment call handed to the model.
        recommendation_confidence=confidence_for_result(cards, combo_note),
    )
