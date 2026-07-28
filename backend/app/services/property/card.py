"""PropertyCard -- a compact, formatting-layer projection of a Property row.

Never send a raw ORM Property (or its full free-text fields -- house_rules,
neighborhood_info, faq) into a guest-facing pitch; a PropertyCard carries
only what's needed to speak a natural recommendation line. build_property_card
also centralizes the spoken_name/display_name/raw_name/name fallback chain in
exactly one place, so every caller (the pitch formatter, and any future
context builder) gets identical fallback behavior instead of reimplementing
`property_.spoken_name or property_.name` separately.
"""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class PropertyCard:
    property_id: uuid.UUID
    spoken_name: str
    display_name: str
    city: str | None
    property_type: str | None
    bedroom_count: int | None
    base_price: float
    max_guests: int
    top_amenities: list[str]
    usp: str | None


def build_property_card(property_) -> PropertyCard:
    display_name = property_.display_name or property_.raw_name or property_.name
    spoken_name = property_.spoken_name or display_name
    return PropertyCard(
        property_id=property_.id,
        spoken_name=spoken_name,
        display_name=display_name,
        city=property_.city,
        property_type=property_.property_type,
        bedroom_count=property_.bedroom_count,
        base_price=float(property_.base_price),
        max_guests=property_.max_guests,
        top_amenities=list(property_.amenities[:2]) if property_.amenities else [],
        usp=property_.usp,
    )
