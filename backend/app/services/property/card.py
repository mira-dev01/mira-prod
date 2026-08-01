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

from app.services.amenity_taxonomy import rank_amenities_for_pitch


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
    # Phase 2.1 (documentation/agent-conversation-improvement.md) -- why THIS
    # card matches the guest's own stated criteria, not just a description of
    # the property. Empty when no criteria were given (never a fabricated
    # reason for a no-criteria browse). A REQUIRED field, like every other
    # field above -- callers must pass [] explicitly rather than relying on
    # a default, since a frozen dataclass with a bare mutable-list default
    # would share one list instance across every card that omits it.
    # Populated by match_reasons_for_card below, not by build_property_card
    # itself, since it needs the guest's RecommendPropertiesArgs, which
    # build_property_card's own signature intentionally doesn't take.
    match_reasons: list[str]


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
        top_amenities=rank_amenities_for_pitch(property_.amenities) if property_.amenities else [],
        usp=property_.usp,
        match_reasons=[],
    )


# Cap at 2 -- voice-friendly (requirement #5's "no information dumping"
# applies just as much to a good reason as to a bad one). Checked in this
# fixed priority order so the most concrete/specific reason (an amenity the
# guest actually named) wins a slot over a vaguer one (a generic purpose
# match) when both apply.
_MAX_MATCH_REASONS = 2

_PURPOSE_PHRASES: dict[str, str] = {
    "family": "great for a family trip",
    "friends": "good for a group of friends",
    "couple": "private and quiet, good for a couples trip",
    "honeymoon": "private and quiet, good for a honeymoon",
    "romantic": "private and quiet, good for a romantic getaway",
    "workcation": "quiet enough to get work done",
    "business": "convenient for a work trip",
    "solo": "a comfortable spot for a solo stay",
}


def _purpose_phrase(purpose_of_stay: str) -> str | None:
    lowered = purpose_of_stay.lower()
    for keyword, phrase in _PURPOSE_PHRASES.items():
        if keyword in lowered:
            return phrase
    return None


def match_reasons_for_card(card: PropertyCard, args) -> list[str]:
    """Compares a card's own fields against whichever RecommendPropertiesArgs
    fields the guest's call actually supplied -- deterministic, never a
    fabricated reason for a criterion that wasn't given. `args` is typed
    loosely (RecommendPropertiesArgs) to avoid a schemas-layer import cycle
    with this module; only budget/num_guests/purpose_of_stay/required_amenities
    are read, all optional on that schema already.
    """
    reasons: list[str] = []

    if args.required_amenities:
        canonical_top = {a.lower() for a in card.top_amenities}
        for requested in args.required_amenities:
            if requested.lower() in canonical_top:
                reasons.append(f"has the {requested.lower()} you asked for")
                break

    if len(reasons) < _MAX_MATCH_REASONS and args.purpose_of_stay:
        phrase = _purpose_phrase(args.purpose_of_stay)
        if phrase:
            reasons.append(phrase)

    if len(reasons) < _MAX_MATCH_REASONS and args.num_guests:
        # "Comfortably covers" -- not just barely meets, which would read as
        # a stretch rather than a genuine fit. Matches apply_guest_count_filter's
        # own exact >= check for eligibility; this is a phrasing threshold on
        # top, not a second, stricter filter -- a property that just meets
        # the count is still shown, just without this specific reason.
        if card.max_guests >= args.num_guests:
            reasons.append(f"fits your group of {args.num_guests}")

    if len(reasons) < _MAX_MATCH_REASONS and args.budget:
        if card.base_price <= args.budget * 0.9:
            reasons.append("comfortably within budget")

    return reasons[:_MAX_MATCH_REASONS]
