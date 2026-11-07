"""Pure text helpers shared by the gazetteer loader and the live lookup, so a
name is always keyed the same way when it is stored and when it is searched."""

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# Words a guest wraps around a place ("near Kasol", "somewhere around Manali").
_FILLER = re.compile(
    r"^(?:(?:in|at|near|nearby|around|close|to|outside|outskirts|of|somewhere|anywhere|the|any|a)\s+)+"
)
_ASPIRATES = (("ph", "p"), ("kh", "k"), ("gh", "g"), ("th", "t"), ("dh", "d"), ("bh", "b"),
              ("sh", "s"), ("ch", "c"), ("ck", "k"), ("ee", "i"), ("oo", "u"))
_LETTER_MAP = str.maketrans({"w": "v", "z": "s", "q": "k", "c": "k", "x": "s"})
_VOWELS = re.compile(r"[aeiouy]")
_REPEATS = re.compile(r"(.)\1+")


def normalize(text: str | None) -> str:
    """Lowercase, ASCII-folded, punctuation stripped, single-spaced."""
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    folded = folded.replace("&", " and ")
    return _NON_ALNUM.sub(" ", folded).strip()


def strip_filler(normalized: str) -> str:
    """'near kasol' -> 'kasol'. Expects normalize() output."""
    stripped = _FILLER.sub("", normalized).strip()
    return stripped or normalized


def skeleton(text: str | None) -> str:
    """A spelling-tolerant key: speech-to-text and transliteration disagree on
    vowels and doubled letters ("Kasol"/"Kasaul", "Kullu"/"Kulu"), so keep the
    first letter and the consonants only. Deliberately coarse -- many names
    share a key, and the caller picks among them by distance to the host's
    properties, never by this key alone."""
    s = normalize(text).replace(" ", "")
    if not s:
        return ""
    for src, dst in _ASPIRATES:
        s = s.replace(src, dst)
    s = s.translate(_LETTER_MAP)
    s = s[0] + _VOWELS.sub("", s[1:])
    return _REPEATS.sub(r"\1", s)[:40]
