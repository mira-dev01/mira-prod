"""The one place a property's locality is written.

`properties.locality` is the area a guest names ("Indiranagar"); `properties.locality_key` is the same
word folded for matching, so "Indira Nagar", "indiranagar" and "Indiranagar " are one value. Writing only
one of them leaves search blind to the other, so the API (create and update) and the backfill script all
go through `set_locality`.
"""

from app.services.geo.text import normalize, strip_filler


def clean_locality(value: str | None) -> str | None:
    """The text to show and speak: trimmed, single-spaced, None when blank."""
    cleaned = " ".join((value or "").split())
    return cleaned or None


def locality_key(value: str | None) -> str | None:
    """The matching form of a locality, or None when there is nothing to match on.

    Folds to ASCII, drops filler words ("near"), and joins the words, so a speech-to-text split of one
    word into two ("Indira Nagar") lands on the stored key. A name written only in a script that does not
    fold (Devanagari) has no key; `set_locality` refuses it rather than store a locality search can't find."""
    key = strip_filler(normalize(value)).replace(" ", "")
    return key or None


def set_locality(property_, value: str | None) -> None:
    """Set `locality` and `locality_key` together; a blank value clears both.

    Raises ValueError for text that has no matching key (for example Devanagari only), so the caller can
    ask for the name in English letters, which is what the search matches on."""
    cleaned = clean_locality(value)
    key = locality_key(cleaned)
    if cleaned is not None and key is None:
        raise ValueError("Write the locality in English letters (for example 'Indiranagar').")
    property_.locality = cleaned
    property_.locality_key = key
