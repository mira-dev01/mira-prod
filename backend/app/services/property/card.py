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

from app.services.amenity_taxonomy import canonicalize_amenity, rank_amenities_for_pitch


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
    # How this card differs from the cheapest other card in the SAME result
    # set -- "" when there's nothing to compare (fewer than 2 cards, or no
    # meaningful difference from the cheapest). Same reasoning as
    # match_reasons: populated by comparison_notes below (needs sibling
    # cards, which build_property_card's per-property signature doesn't
    # have access to), a REQUIRED field for the same frozen-dataclass
    # mutable-default reason match_reasons is.
    comparison_note: str
    # Recommendation conversations ("Phase X"): host-set fact (Property.is_premium),
    # never LLM-inferred -- grounds a guest's "something more premium" request
    # in a real signal instead of asking the model to judge which property
    # feels nicer. Unlike match_reasons/comparison_note above, this is a
    # plain column already present on `property_` at construction time (not
    # derived from guest args or sibling cards), so it's populated directly
    # here rather than backfilled later via dataclasses.replace.
    is_premium: bool
    # "has pool but not pet friendly" -- "" when there's nothing partial to
    # report (fewer than 2 requested amenities, or an all-matched/all-missing
    # case). Same reasoning as match_reasons/comparison_note: populated by
    # amenity_checklist_note below, which needs both the guest's accumulated
    # required_amenities AND the property's REAL full amenity_tags (never
    # top_amenities, which is truncated) -- neither is available at
    # build_property_card's own per-property construction time.
    amenity_checklist: str


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
        comparison_note="",
        is_premium=bool(property_.is_premium),
        amenity_checklist="",
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
        # Canonicalized on both sides -- same synonym normalization
        # apply_amenity_boost/amenity_checklist_note already use against the
        # property's real amenity_tags. A raw .lower() substring match here
        # (the previous behavior) missed a synonym like "pet friendly" vs a
        # top_amenities entry scraped as "Pets Allowed", producing a card
        # whose amenity_checklist correctly said "has ... pet friendly" while
        # this clause stayed silent about the same match.
        canonical_top = {canonicalize_amenity(a) for a in card.top_amenities}
        for requested in args.required_amenities:
            if canonicalize_amenity(requested) in canonical_top:
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


def amenity_checklist_note(required_amenities: list[str] | None, real_canonical_amenities: list[str]) -> str:
    """Recommendation conversations ("Phase X"): required_amenities is now a
    SOFT ranking preference (filter_builder.apply_amenity_boost), not a hard
    filter -- a returned property can genuinely have SOME but not ALL of a
    guest's accumulated amenity requests (e.g. "pool" then, later, "pet
    friendly"). Per explicit product direction: when that happens, state
    BOTH which requested amenities the property has AND which it doesn't,
    explicitly, so the guest can decide for themselves rather than the
    filter silently deciding for them. Only returns a note (non-"") when
    there are 2+ requested amenities AND the match is genuinely partial --
    a single requested amenity is already covered by match_reasons_for_card's
    own "has the X you asked for" clause above (no need to duplicate it),
    and an all-matched or all-missing case needs no explicit checklist
    either (all-matched already reads as a clean fit via the existing
    reason clause; all-missing is already rare now that the boost still
    ranks a zero-match property last, and stating a checklist of nothing
    present would read oddly). real_canonical_amenities is the property's
    REAL, FULL amenity_tags list (never PropertyCard.top_amenities, which
    is deliberately truncated to 2 for pitch brevity and would produce a
    false "doesn't have it" for an amenity outside that top-2).
    """
    if not required_amenities or len(required_amenities) < 2:
        return ""

    canonical_real = set(real_canonical_amenities)
    present = []
    missing = []
    for requested in required_amenities:
        canonical = canonicalize_amenity(requested)
        if canonical in canonical_real:
            present.append(requested)
        else:
            missing.append(requested)

    if not present or not missing:
        return ""

    # Deliberately a plain " and "-join here rather than importing
    # pitch_formatter._join_natural -- pitch_formatter already imports FROM
    # card.py (PropertyCard itself), so importing back the other way would
    # create a cross-module coupling for one trivial join, not worth it for
    # a helper this small.
    present_text = " and ".join(p.lower() for p in present)
    missing_text = " and ".join(m.lower() for m in missing)
    return f"has {present_text} but not {missing_text}"


# "Meaningfully" different, not any nonzero gap -- a ₹200 price difference or
# a 1-guest capacity difference isn't worth voicing as a reason to pick one
# over another; these thresholds keep the note reserved for a difference a
# guest would actually care about. Percentage-based for price (a flat rupee
# threshold would be wrong at both a ₹2,000/night and a ₹20,000/night
# property); flat for guest count (capacity differences are already small,
# discrete numbers -- a percentage would misfire between e.g. 2 and 3 guests).
_MEANINGFUL_PRICE_GAP_RATIO = 0.15
_MEANINGFUL_GUEST_GAP = 2


def comparison_notes(
    cards: list[PropertyCard], unreliable_price_ids: frozenset[uuid.UUID] = frozenset()
) -> dict[uuid.UUID, str]:
    """For each card, one clause naming the clearest way it differs from the
    CHEAPEST other card in the same result set -- grounded in real
    already-known PropertyCard fields, never a fabricated or LLM-guessed
    comparison, same discipline as match_reasons_for_card above. This is
    what answers a guest's own follow-up ("why not the other one?", "what's
    the difference?") with a real fact instead of leaving the model to
    invent or misstate one -- the exact class of failure
    property_recommendation_guard.py's existing price/capacity fidelity
    checks already guard against for single-card facts.

    unreliable_price_ids: property_ids whose Property.exact_airbnb_pricing is
    True -- filter_builder.build_base_filters deliberately lets these through
    regardless of their stored base_price (their real price comes from a live
    SearchApi fetch at get_pricing time instead, so base_price can be stale,
    a placeholder, or 0 -- confirmed by reading that filter directly). A card
    in this set is NEVER used for a price comparison, as either the cheapest
    baseline or the other side of a gap -- only its capacity may be compared.
    Comparing against an unverified stored price is exactly the class of
    failure this codebase has already been burned by once (a base_price=0
    property spoken as "free of charge," project_state.md's 2026-07-23 entry)
    and already built a dedicated guard against for get_pricing/negotiate_rate
    -- this function must not reopen the same failure shape in a new place.

    Returns {} (no notes at all) when there's nothing real to compare: fewer
    than 2 cards with a usable price (nothing to differ from), or every
    remaining candidate's price is unusable (non-positive or flagged
    unreliable) -- the same zero-price properties are already excluded
    upstream by filter_builder.build_base_filters, but this stays defensive
    rather than assuming that guarantee holds for every future caller.

    Cheapest-as-baseline (not e.g. every pair): deterministic and matches
    how a guest naturally anchors when comparing options out loud -- "how
    does this one compare to the cheapest?" rather than an every-pair
    matrix, which would also risk multiple, possibly conflicting clauses
    per card. Only ONE clause per card (price OR capacity, price checked
    first as the more universally-relevant fact) -- same voice-friendly
    "one clause, never a second sentence" discipline match_reasons_for_card
    and its own reason_clause wiring in pitch_formatter.py already use.
    """
    if len(cards) < 2:
        return {}

    priced_cards = [c for c in cards if c.property_id not in unreliable_price_ids and c.base_price > 0]
    cheapest = min(priced_cards, key=lambda c: c.base_price) if priced_cards else None

    notes: dict[uuid.UUID, str] = {}
    for card in cards:
        if cheapest is not None and card.property_id == cheapest.property_id:
            continue

        if cheapest is not None and card.property_id not in unreliable_price_ids:
            price_gap_ratio = (card.base_price - cheapest.base_price) / cheapest.base_price
            if price_gap_ratio >= _MEANINGFUL_PRICE_GAP_RATIO:
                extra = card.base_price - cheapest.base_price
                notes[card.property_id] = f"₹{extra:,.0f} more than {cheapest.spoken_name} a night"
                continue

        if cheapest is None:
            continue

        # Capacity difference in EITHER direction is worth voicing -- a
        # pricier-but-smaller option (a common real shape: a large cheap
        # family villa vs. a small pricier boutique unit) is just as
        # relevant a tradeoff as a pricier-and-bigger one. Phrasing flips
        # to match the real direction rather than only ever saying "more."
        guest_gap = card.max_guests - cheapest.max_guests
        if abs(guest_gap) >= _MEANINGFUL_GUEST_GAP:
            direction = "more" if guest_gap > 0 else "fewer"
            notes[card.property_id] = f"sleeps {abs(guest_gap)} {direction} than {cheapest.spoken_name}"

    return notes
