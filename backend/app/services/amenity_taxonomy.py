"""Small fixed synonym map for canonicalizing free-text amenity strings
(e.g. "Private pool" / "Swimming pool" / "Shared pool" -> "pool") so
recommend_properties can filter on an exact canonical value instead of an
inconsistent ILIKE over Bright Data's/host's own free-text amenity names.

Deliberately NOT a full taxonomy service -- a fixed dict, extended as real
data reveals more variants. An amenity string with no known synonym just
canonicalizes to its own lowercased form, so it still round-trips through
Property.amenity_tags (a host's oddly-worded amenity just won't
canonical-match someone else's differently-worded version of the same
thing -- an acceptable gap, not a correctness bug, since the free-text
`amenities` column is unaffected and still used for display).
"""

_AMENITY_SYNONYMS: dict[str, str] = {
    "private pool": "pool",
    "swimming pool": "pool",
    "shared pool": "pool",
    "pool": "pool",
    "wifi": "wifi",
    "wi-fi": "wifi",
    "free wifi": "wifi",
    "internet": "wifi",
    "air conditioning": "ac",
    "ac": "ac",
    "air conditioner": "ac",
    "parking": "parking",
    "free parking": "parking",
    "private parking": "parking",
    "kitchen": "kitchen",
    "full kitchen": "kitchen",
    "kitchenette": "kitchen",
    "washing machine": "washer",
    "washer": "washer",
    "pet friendly": "pets_allowed",
    "pets allowed": "pets_allowed",
    "bathtub": "bathtub",
    "jacuzzi": "bathtub",
    "hot tub": "bathtub",
    "sea view": "view",
    "ocean view": "view",
    "mountain view": "view",
    "garden view": "view",
    "projector": "projector",
    "tv": "tv",
    "television": "tv",
    "workspace": "workspace",
    "dedicated workspace": "workspace",
}


def canonicalize_amenity(raw: str) -> str:
    return _AMENITY_SYNONYMS.get(raw.strip().lower(), raw.strip().lower())


def canonicalize_amenities(raw_amenities: list[str]) -> list[str]:
    return sorted({canonicalize_amenity(a) for a in raw_amenities if a and a.strip()})


# Notable/differentiating amenities a guest would actually want called out
# in a spoken pitch (a private pool, a projector) -- ranked ahead of
# amenities most listings have as a baseline (wifi, ac, kitchen, parking,
# tv) which add no distinguishing signal when speaking a recommendation.
# Confirmed live 2026-07-28: without this ranking, top_amenities picked
# whichever two amenities happened to be scraped first (e.g. "Bath" and
# "Hairdryer"), while the property's own listing title advertised "pool"
# and "projector" as its actual selling points -- a guest never heard the
# thing that made the property worth choosing.
_NOTABLE_AMENITY_TAGS = {
    "pool", "bathtub", "projector", "view", "pets_allowed", "workspace",
}


def rank_amenities_for_pitch(raw_amenities: list[str], limit: int = 2) -> list[str]:
    """Picks up to `limit` amenities from a property's free-text amenities
    list, preferring notable/differentiating ones (pool, bathtub, view,
    projector, ...) over generic baseline ones (wifi, ac, kitchen, parking,
    tv). Ties within a tier keep their original scrape order. Returns the
    original (non-canonicalized) amenity strings, since these are for
    display/speech, not facet matching."""
    notable = [a for a in raw_amenities if canonicalize_amenity(a) in _NOTABLE_AMENITY_TAGS]
    generic = [a for a in raw_amenities if canonicalize_amenity(a) not in _NOTABLE_AMENITY_TAGS]
    return (notable + generic)[:limit]
