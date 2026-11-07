"""Indian states/UTs by the names guests actually use. Names are GeoNames'
admin1 names (what Property.state stores), so a state query is an exact match
on a controlled value. Deliberately omits ambiguous short forms ("up", "mp",
"ap" -- ordinary words) and keeps only unambiguous ones."""

from app.services.geo.text import normalize

_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chandigarh", "Chhattisgarh", "Delhi", "Goa",
    "Gujarat", "Haryana", "Himachal Pradesh", "Jammu and Kashmir", "Jharkhand", "Karnataka", "Kerala",
    "Ladakh", "Lakshadweep", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland",
    "Odisha", "Puducherry", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman and Nicobar",
]

_EXTRA_ALIASES = {
    "himachal": "Himachal Pradesh", "hp": "Himachal Pradesh",
    "kashmir": "Jammu and Kashmir", "jammu": "Jammu and Kashmir", "jammu kashmir": "Jammu and Kashmir",
    "j and k": "Jammu and Kashmir", "jk": "Jammu and Kashmir",
    "uttaranchal": "Uttarakhand", "orissa": "Odisha", "pondicherry": "Puducherry",
    "bengal": "West Bengal", "tamilnadu": "Tamil Nadu", "new delhi": "Delhi", "andaman": "Andaman and Nicobar",
}

STATE_BY_ALIAS: dict[str, str] = {normalize(s): s for s in _STATES}
STATE_BY_ALIAS.update({normalize(alias): state for alias, state in _EXTRA_ALIASES.items()})


def match_state(text: str | None) -> str | None:
    """'Himachal' / 'Himachal Pradesh state' -> 'Himachal Pradesh'; else None."""
    n = normalize(text)
    if n.endswith(" state"):
        n = n[: -len(" state")].strip()
    return STATE_BY_ALIAS.get(n)
