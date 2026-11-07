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


async def list_areas(db: AsyncSession, host_user_id: uuid.UUID) -> list[str]:
    """States the host has properties in, most properties first -- what to
    offer a guest when their area has nothing."""
    rows = (
        await db.execute(
            select(Property.state, func.count())
            .where(Property.user_id == host_user_id, Property.state.is_not(None))
            .group_by(Property.state)
            .order_by(func.count().desc(), Property.state)
        )
    ).all()
    return [state for state, _ in rows]
