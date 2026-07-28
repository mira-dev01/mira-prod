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
