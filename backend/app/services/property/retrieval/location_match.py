"""Which of a host's own areas the guest's words name.

The location filter used to ILIKE the guest's phrase against `city` and `neighborhood_info`: it never saw a
locality, and a substring cannot join "Indira Nagar" to "Indiranagar" (call 56947e99, 2026-11-13). This resolves
the phrase against the host's OWN localities and cities, in this order:

  1. exact -- the words, run together, equal a stored `locality_key`;
  2. skeleton -- the same after the spelling-tolerant fold in geo/text.py ("Indra Nagar", "Kormangala"), but
     only against this host's localities, never a global list, so a near-miss can only land on a place the
     host really has;
  3. prefix -- the guest gave the first word(s) of an area ("Bandra" for "Bandra West", "HSR" for
     "HSR Layout"); only when exactly one of the host's areas starts that way;
  4. city -- the guest named just a city the host has (nothing else in the phrase).

If the phrase names a city the host has and the area exists only in a different city, nothing resolves
("Indiranagar Mumbai" is not the Bengaluru Indiranagar).

An unknown word never resolves ("Juhu Bangalore" is `none`, not "Bengaluru"): the caller then keeps the old
free-text match, which finds nothing and reports the area as not on file.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property import Property
from app.services.geo.text import normalize, skeleton, strip_filler
from app.services.property.locality import locality_key

_MIN_SKELETON_LEN = 5
_MIN_PREFIX_LEN = 3
LOCALITY_VIAS = ("locality", "locality_skeleton", "locality_prefix")
# A locality is rarely more than three words; STT may split one word in two, hence the slack.
_MAX_WINDOW = 5


@dataclass(frozen=True)
class LocationMatch:
    city: str | None = None
    locality: str | None = None
    # "locality" | "locality_skeleton" | "locality_prefix" | "city" | "none"
    via: str = "none"

    @property
    def is_locality(self) -> bool:
        """The guest's words named one of the host's own areas (not just a city, not nothing)."""
        return self.via in LOCALITY_VIAS

    @property
    def locality_key(self) -> str | None:
        return locality_key(self.locality)


NO_MATCH = LocationMatch()


def _city_keys(city: str) -> set[str]:
    """The city's own name and its other common spelling, run together ("bengaluru", "bangalore")."""
    from app.services.property.retrieval.filter_builder import _CITY_SYNONYMS  # not at module level: it imports this module

    names = [city, *_CITY_SYNONYMS.get(city.strip().lower(), [])]
    return {k for k in (strip_filler(normalize(n)).replace(" ", "") for n in names) if k}


def same_city(a: str | None, b: str | None) -> bool:
    """Two city spellings name the same city ("Bangalore" / "Bengaluru", "bengaluru "), or False when either is blank."""
    return bool(a and b and _city_keys(a) & _city_keys(b))


def resolve(
    text: str | None,
    host_localities: Iterable[tuple[str, str | None]],
    host_cities: Iterable[str],
) -> LocationMatch:
    """`host_localities` is (locality, its city) for each distinct locality the host has set."""
    tokens = strip_filler(normalize(text)).split()
    if not tokens:
        return NO_MATCH

    localities = [(name, city, locality_key(name)) for name, city in host_localities]
    localities = [entry for entry in localities if entry[2]]
    cities = list(host_cities)

    windows = [
        (start, size)
        for size in range(min(len(tokens), _MAX_WINDOW), 0, -1)
        for start in range(len(tokens) - size + 1)
    ]

    named_city_keys: set[str] = set()
    city_span: set[int] = set()
    for start, size in windows:
        compact = "".join(tokens[start : start + size])
        for city in cities:
            if compact in _city_keys(city):
                named_city_keys |= _city_keys(city)
                city_span |= set(range(start, start + size))

    def pick(candidates: list[tuple[str, str | None, str]], via: str) -> LocationMatch:
        # The phrase may also name the city ("Indiranagar Bangalore"): prefer the locality in that city.
        in_named_city = [c for c in candidates if c[1] and _city_keys(c[1]) & named_city_keys]
        if named_city_keys and not in_named_city:
            return NO_MATCH  # they named a city the host has, and this area is not in it
        name, city, _ = sorted(in_named_city or candidates, key=lambda c: (c[0].lower(), c[1] or ""))[0]
        return LocationMatch(city=city, locality=name, via=via)

    by_key: dict[str, list[tuple[str, str | None, str]]] = {}
    by_skeleton: dict[str, list[tuple[str, str | None, str]]] = {}
    for entry in localities:
        by_key.setdefault(entry[2], []).append(entry)
        sk = skeleton(entry[0])
        if len(sk) >= _MIN_SKELETON_LEN:
            by_skeleton.setdefault(sk, []).append(entry)

    for start, size in windows:  # longest phrase first, so "hsr layout" beats a shorter accident
        if set(range(start, start + size)) <= city_span:
            continue  # the words are the city's own name, not an area inside it
        window = tokens[start : start + size]
        hit = by_key.get("".join(window))
        if hit:
            return pick(hit, "locality")
    for start, size in windows:
        if set(range(start, start + size)) <= city_span:
            continue
        sk = skeleton(" ".join(tokens[start : start + size]))
        if len(sk) >= _MIN_SKELETON_LEN and sk in by_skeleton:
            return pick(by_skeleton[sk], "locality_skeleton")

    # The first word(s) of an area: "Bandra" for "Bandra West". Never a middle or last word ("West",
    # "Layout" name many areas), and only when a single area starts that way.
    by_prefix: dict[str, set[str]] = {}
    for entry in localities:
        words = strip_filler(normalize(entry[0])).split()
        for k in range(1, len(words)):
            by_prefix.setdefault("".join(words[:k]), set()).add(entry[2])
    entries_by_key = {entry[2]: [e for e in localities if e[2] == entry[2]] for entry in localities}
    for start, size in windows:
        if set(range(start, start + size)) <= city_span:
            continue
        compact = "".join(tokens[start : start + size])
        keys = by_prefix.get(compact) if len(compact) >= _MIN_PREFIX_LEN else None
        if keys and len(keys) == 1:
            return pick(entries_by_key[next(iter(keys))], "locality_prefix")

    # City only: the guest named a city and nothing else.
    if named_city_keys and city_span == set(range(len(tokens))):
        for city in cities:
            if _city_keys(city) & named_city_keys:
                return LocationMatch(city=city, via="city")
    return NO_MATCH


def same_area(a: str | None, b: str | None) -> bool:
    """Two phrases name the same area: equal once run together ("Indira Nagar" = "Indiranagar"), equal under
    the spelling-tolerant fold ("Indra Nagar"), or one is the other plus more words ("Goa" / "North Goa")."""
    ka = strip_filler(normalize(a)).replace(" ", "")
    kb = strip_filler(normalize(b)).replace(" ", "")
    if not ka or not kb:
        return False
    if ka == kb or ka in kb or kb in ka:
        return True
    sa, sb = skeleton(a), skeleton(b)
    return len(sa) >= _MIN_SKELETON_LEN and sa == sb


@dataclass(frozen=True)
class HostPlaces:
    localities: tuple[tuple[str, str | None], ...] = ()
    cities: tuple[str, ...] = ()


async def load_host_places(db: AsyncSession, host_user_id: uuid.UUID) -> HostPlaces:
    """The host's distinct (locality, city) pairs and cities. Small: one grouped query."""
    rows = (
        await db.execute(
            select(Property.locality, Property.city)
            .where(Property.user_id == host_user_id)
            .group_by(Property.locality, Property.city)
        )
    ).all()
    localities = tuple(sorted({(loc, city) for loc, city in rows if loc}, key=lambda r: (r[0].lower(), r[1] or "")))
    cities = tuple(sorted({city for _, city in rows if city}, key=str.lower))
    return HostPlaces(localities=localities, cities=cities)
