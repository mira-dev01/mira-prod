"""Turns a RecommendPropertiesArgs into SQLAlchemy WHERE clauses -- pure,
no I/O. Moved verbatim out of app/services/tool_handlers.py's
handle_recommend_properties (see git history) as part of splitting that
function into focused, single-responsibility modules; the filtering logic
itself is unchanged.
"""

import uuid

from sqlalchemy import Select, or_, select

from app.models.property import Property
from app.schemas.tool import RecommendPropertiesArgs
from app.services.amenity_taxonomy import canonicalize_amenities

# Property.city/neighborhood_info are free text -- most properties only ever
# say "Goa" or one specific locality ("Colva", "Siolim"), never the literal
# words "North Goa"/"South Goa" (confirmed live: Azure's city is "Colva" and
# its neighborhood_info never mentions "South Goa" either, so an ILIKE match
# on those exact words silently excluded it from a "South Goa" query even
# though Colva unambiguously is South Goa). This maps the common named
# localities to their region so a region query matches on where a property
# actually is, not on whether that exact phrase happens to appear in its text.
_GOA_NORTH_LOCALITIES = [
    "siolim", "anjuna", "vagator", "chapora", "morjim", "ashwem", "mandrem",
    "calangute", "candolim", "baga", "arpora", "assagao", "mapusa",
    "sinquerim", "arambol", "querim", "reis magos",
]
_GOA_SOUTH_LOCALITIES = [
    "colva", "margao", "madgaon", "benaulim", "varca", "cavelossim", "mobor",
    "palolem", "agonda", "majorda", "cansaulim", "betalbatim", "velsao",
    "vasco", "bogmalo", "canacona",
]


def _goa_region_localities(preferred_location: str) -> list[str] | None:
    normalized = preferred_location.strip().lower()
    if "north goa" in normalized:
        return _GOA_NORTH_LOCALITIES
    if "south goa" in normalized:
        return _GOA_SOUTH_LOCALITIES
    return None


def build_base_filters(args: RecommendPropertiesArgs, host_user_id: uuid.UUID) -> Select:
    """Budget/location/amenity filters only -- deliberately excludes the
    guest-count filter (see build_guest_count_filter below), so callers can
    reuse this as the "everything except how many people" base statement
    for the small-units-fallback path (today's "no single property sleeps
    everyone" behavior)."""
    stmt = select(Property).where(Property.user_id == host_user_id)

    # Phase 2.0 (documentation/agent-conversation-improvement.md, catalogue
    # item C2): never recommend a property with no real nightly rate on
    # file. Confirmed live: a base_price=0 property was pitched to a guest
    # as "free of charge per night" during recommend_properties -- a
    # DIFFERENT, previously-unguarded path from the existing ₹0 guard in
    # handle_get_pricing/handle_negotiate_rate (project_state.md's
    # 2026-07-23 fix), which only covers the pricing tool, not the
    # recommendation pitch (pitch_formatter.py reads PropertyCard.base_price
    # directly with no equivalent check). Scoped to exact_airbnb_pricing=False
    # only: a True property's real price comes from a live SearchApi fetch
    # at get_pricing time regardless of what base_price says, so a stale/
    # unset base_price there is harmless and must not hide an otherwise-real,
    # working listing. This must also NOT be folded into the budget filter
    # below -- base_price=0 passes ANY "<=" budget check trivially, so the
    # bug can surface even when a budget filter is active; it needs its own
    # unconditional clause.
    stmt = stmt.where(or_(Property.exact_airbnb_pricing.is_(True), Property.base_price > 0))

    if args.budget is not None:
        stmt = stmt.where(Property.base_price <= args.budget * 1.15)

    if args.preferred_location:
        # Match against city name OR neighborhood_info so state-level queries
        # ("Kerala", "Himachal") find properties whose city is e.g. "Alleppey"
        # but whose neighborhood text mentions the broader region.
        localities = _goa_region_localities(args.preferred_location)
        if localities:
            # For "north/south goa" specifically, match ONLY against the
            # expanded locality list -- never the literal "South Goa"/
            # "North Goa" phrase against neighborhood_info free text.
            # Confirmed live 2026-07-27: several North Goa (Siolim)
            # properties' neighborhood_info mentions "Dabolim (South Goa
            # airport)" purely as a travel-time reference point, and a
            # literal "%South Goa%" substring match against that text
            # wrongly treated the mention as evidence the property itself is
            # in South Goa -- a guest who explicitly asked for South Goa was
            # recommended a Siolim (North Goa) property because of it.
            location_filter = or_(
                *[
                    or_(Property.city.ilike(f"%{locality}%"), Property.neighborhood_info.ilike(f"%{locality}%"))
                    for locality in localities
                ]
            )
        else:
            loc = f"%{args.preferred_location}%"
            location_filter = or_(Property.city.ilike(loc), Property.neighborhood_info.ilike(loc))
        stmt = stmt.where(location_filter)

    # required_amenities is a SOFT ranking preference (see apply_amenity_boost
    # below), not a hard WHERE filter -- deliberately changed from an earlier
    # hard `amenity_tags.contains()` clause. Recommendation conversations
    # ("Phase X"): once a guest has accumulated more than one requested
    # amenity across a conversation ("pool" then, later, "pet friendly"), a
    # property matching the first but not the second used to be excluded
    # from results entirely, with no way to tell the guest it was a close
    # partial match -- they'd just never hear about it. A soft boost (same
    # "rank first, never exclude to zero" pattern apply_landmark_boost and
    # apply_premium_boost already use) surfaces it, and card.py's
    # amenity_checklist_note tells the guest explicitly which of the
    # requested amenities it does/doesn't have, so they can decide for
    # themselves rather than the filter silently deciding for them.

    return stmt


def apply_guest_count_filter(stmt: Select, args: RecommendPropertiesArgs) -> Select:
    if args.num_guests is not None:
        stmt = stmt.where(Property.max_guests >= args.num_guests)
    return stmt


# Empirically reasonable starting threshold for "close enough to count as
# the same landmark name" -- guest speech-to-text will rarely spell a
# landmark name back exactly (e.g. "Thalassa" vs "Thalasa"), but this needs
# real-call tuning the same way embedding_service.SEMANTIC_MATCH_THRESHOLD
# was validated against real question pairs; treat as a starting point.
_LANDMARK_MATCH_THRESHOLD = 0.6


def matches_landmark(property_: Property, landmark_query: str) -> bool:
    """Python-side fuzzy match against a property's own small `landmarks`
    list -- deliberately not a SQL/JSONB-path predicate. The candidate set
    per host is tiny (a handful of properties, a handful of landmarks
    each), so there's no performance reason to push this into pg_trgm/
    JSONB-path SQL, and matching in Python avoids that fragility entirely.
    Falls back to a plain neighborhood_info substring match for properties
    with no structured landmark data yet."""
    import difflib

    query = landmark_query.strip().lower()
    if not query:
        return False

    for landmark in property_.landmarks or []:
        name = (landmark.get("name") or "").strip().lower()
        if not name:
            continue
        if difflib.SequenceMatcher(None, query, name).ratio() >= _LANDMARK_MATCH_THRESHOLD:
            return True

    if not property_.landmarks and property_.neighborhood_info:
        return query in property_.neighborhood_info.lower()

    return False


def apply_landmark_boost(properties: list[Property], near_landmark: str | None) -> list[Property]:
    """Soft rank signal, not a hard filter -- a property lacking a landmark
    match isn't excluded, just not boosted ahead of others. Never drops
    every result to zero just because nobody has structured/matching
    landmark data yet."""
    if not near_landmark:
        return properties
    return sorted(properties, key=lambda p: not matches_landmark(p, near_landmark))


def apply_amenity_boost(properties: list[Property], required_amenities: list[str] | None) -> list[Property]:
    """Soft rank signal, same shape as apply_landmark_boost/apply_premium_boost
    -- ranks by how MANY of the requested amenities each property actually
    has (most matches first), never excludes a property for lacking one.
    Deliberately replaced an earlier hard `amenity_tags.contains()` WHERE
    filter (see build_base_filters above) so a property matching some but
    not all of an accumulated multi-amenity request still surfaces instead
    of silently disappearing -- card.py's amenity_checklist_note is what
    tells the guest explicitly which requested amenities each result does
    and doesn't have, so a partial match is a real, informed choice for the
    guest rather than a filter deciding for them."""
    if not required_amenities:
        return properties
    canonical_required = set(canonicalize_amenities(required_amenities))
    return sorted(
        properties,
        key=lambda p: -len(canonical_required & set(p.amenity_tags or [])),
    )


def apply_premium_boost(properties: list[Property], more_premium_than_shown: bool) -> list[Property]:
    """Same soft-rank-not-hard-filter shape as apply_landmark_boost above --
    a guest asking for "something more premium" gets Property.is_premium
    properties ranked first, but a host with zero is_premium properties set
    (the default, since it's opt-in) still gets a normal, non-empty result
    rather than nothing. Grounded in a real host-set fact, never an
    LLM-guessed judgment of which property "feels" nicer -- the same
    discipline this codebase already applies everywhere else a guest-facing
    quality claim is made (comparison_notes, match_reasons)."""
    if not more_premium_than_shown:
        return properties
    return sorted(properties, key=lambda p: not p.is_premium)
