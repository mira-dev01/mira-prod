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

from dataclasses import dataclass

from app.services.property.card import PropertyCard

_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


@dataclass
class RecommendationResult:
    options: list[PropertyCard]
    combo_note: str = ""
    not_found: bool = False


_NOT_FOUND_TEXT = "I couldn't find a property in our portfolio matching that -- let me connect you with the host directly."


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

    return (
        f"{index}. {card.spoken_name}, a {descriptor}{amenity_phrase} in {card.city or 'unlisted city'} "
        f"for ₹{card.base_price:,.0f} a night, sleeps {card.max_guests}. (property_id: {card.property_id})"
    )


def render_recommendation_text(result: RecommendationResult) -> str:
    if result.not_found or not result.options:
        return _NOT_FOUND_TEXT

    # One property per line, newline-joined -- never " | "-joined. Real
    # property names routinely contain a literal "|" themselves (e.g.
    # imported Airbnb titles like "Azure 1bhk | 5 mins walk to beach |
    # Pause Project"); a newline can never appear inside a single-line DB
    # field, so it can't collide with a name's own delimiters the way " | "
    # did (confirmed live 2026-07-27).
    lines = [format_property_pitch_line(card, i) for i, card in enumerate(result.options, 1)]
    return "Here are some options:\n" + "\n".join(lines) + result.combo_note
