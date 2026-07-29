"""list[Property] -> RecommendationResult -- reuses Phase 3's
build_property_card so the retrieval pipeline and the guard/pitch-formatter
boundary established in Phase 3 stay the single source of truth for how a
Property becomes guest-facing content.
"""

from app.models.property import Property
from app.services.property.card import build_property_card
from app.services.property.pitch_formatter import RecommendationResult


def build_recommendation_result(properties: list[Property], combo_note: str = "") -> RecommendationResult:
    if not properties:
        return RecommendationResult(options=[], not_found=True)
    return RecommendationResult(
        options=[build_property_card(p) for p in properties],
        combo_note=combo_note,
    )
