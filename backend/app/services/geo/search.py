"""Which of a host's properties are in/near the area a guest named. Used by
recommend_properties as an addition to (never a replacement for) the existing
free-text location match."""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property import Property
from app.services.geo.distance import haversine_km
from app.services.geo.gazetteer import NEAR_RADIUS_KM, ResolvedLocation, resolve_location
from app.services.geo.text import normalize

# Second ring, used only when nothing is within NEAR_RADIUS_KM: still worth
# offering ("closest we have is about 90 km away") but flagged as farther.
FARTHER_RADIUS_KM = 150.0


@dataclass
class GeoMatch:
    location: ResolvedLocation
    property_ids: set[uuid.UUID] = field(default_factory=set)
    # Straight-line km from the named place, for properties matched by position.
    distances_km: dict[uuid.UUID, float] = field(default_factory=dict)
    # True when only the wider ring had properties.
    farther: bool = False

    @property
    def unlocated(self) -> ColumnElement[bool]:
        """Properties this match could not judge (no point / no state on file).
        Only these may still be matched by free text: a property WITH a location
        is decided by where it is, so a description that merely mentions the area
        ("a bit like Ooty") cannot pull in a villa 300 km away."""
        return Property.state.is_(None) if self.location.kind == "state" else Property.latitude.is_(None)


async def match_properties(db: AsyncSession, host_user_id: uuid.UUID, phrase: str | None) -> GeoMatch | None:
    """None when nothing could be resolved or the host's properties have no
    location data yet -- the caller then relies on the text match alone."""
    if not phrase or not phrase.strip():
        return None
    rows = (
        await db.execute(
            select(Property.id, Property.latitude, Property.longitude, Property.state).where(
                Property.user_id == host_user_id
            )
        )
    ).all()
    located = [(pid, float(lat), float(lng)) for pid, lat, lng, _ in rows if lat is not None and lng is not None]
    if not located and not any(state for *_, state in rows):
        return None

    location = await resolve_location(db, phrase, [(lat, lng) for _, lat, lng in located])
    if location is None:
        return None

    if location.kind == "state":
        ids = {pid for pid, _, _, state in rows if state == location.state}
        return GeoMatch(location, property_ids=ids)

    distances = {pid: haversine_km(location.lat, location.lng, lat, lng) for pid, lat, lng in located}
    near = {pid: d for pid, d in distances.items() if d <= NEAR_RADIUS_KM}
    if near:
        return GeoMatch(location, set(near), near)
    far = {pid: d for pid, d in distances.items() if d <= FARTHER_RADIUS_KM}
    return GeoMatch(location, set(far), far, farther=bool(far))


async def list_areas(db: AsyncSession, host_user_id: uuid.UUID) -> list[tuple[str, int]]:
    """The host's cities with how many properties each has, most first -- what to offer a
    guest whose area has nothing. City, not state: every property has a city, while state
    is only filled once a property has a map point."""
    rows = (
        await db.execute(
            select(Property.city, func.count())
            .where(Property.user_id == host_user_id, Property.city.is_not(None))
            .group_by(Property.city)
        )
    ).all()
    merged: dict[str, tuple[str, int]] = {}  # "Bengaluru" and "bengaluru " are one city
    # The spelling shown is the most common one (then alphabetical), never whichever row the database
    # happened to return first: the same call must always name a city the same way.
    for city, count in sorted(rows, key=lambda row: (-row[1], row[0] or "")):
        name = (city or "").strip()
        key = normalize(name)
        if key:
            shown, total = merged.get(key, (name, 0))
            merged[key] = (shown, total + count)
    return sorted(merged.values(), key=lambda area: (-area[1], area[0]))


async def list_localities(db: AsyncSession, host_user_id: uuid.UUID) -> dict[str, list[tuple[str, int]]]:
    """The host's areas inside each city: {normalized city: [(locality, property count), ...]}, most
    properties first then alphabetical, so the same host always produces the same text. A city whose
    properties have no locality set has no entry (the caller falls back to the city alone)."""
    rows = (
        await db.execute(
            select(Property.city, Property.locality, Property.locality_key, func.count())
            .where(Property.user_id == host_user_id, Property.city.is_not(None), Property.locality_key.is_not(None))
            .group_by(Property.city, Property.locality, Property.locality_key)
        )
    ).all()
    merged: dict[str, dict[str, tuple[str, int]]] = {}
    # Spelling shown for a locality: its most common one, then alphabetical.
    for city, locality, key, count in sorted(rows, key=lambda r: (-r[3], r[1] or "")):
        city_key = normalize(city)
        if not city_key or not key:
            continue
        shown, total = merged.setdefault(city_key, {}).get(key, (locality, 0))
        merged[city_key][key] = (shown, total + count)
    return {
        city_key: sorted(by_key.values(), key=lambda a: (-a[1], a[0].lower()))
        for city_key, by_key in merged.items()
    }
