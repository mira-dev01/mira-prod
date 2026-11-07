"""Writing a property's location: set an authoritative point (the host's
website or map pin) and derive state/district from it, or make a conservative
guess from the property's own text when nothing authoritative exists."""

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property import Property
from app.services.geo.gazetteer import TOWN_POPULATION, _exact_candidates, nearest_place
from app.services.geo.text import normalize, strip_filler

logger = logging.getLogger(__name__)

_NEAR_IN_NAME = re.compile(r"\bnear\s+([A-Za-z][A-Za-z .'-]{2,40}?)(?:\s*[,|:(\-]|$)", re.IGNORECASE)


async def set_property_point(db: AsyncSession, prop: Property, lat: float, lng: float, source: str) -> None:
    """Store an authoritative point and derive state/district from it. Never
    raises on a missing gazetteer -- the point alone is still useful."""
    prop.latitude = round(float(lat), 6)
    prop.longitude = round(float(lng), 6)
    prop.location_source = source
    near = await nearest_place(db, prop.latitude, prop.longitude)
    if near is not None:
        prop.state = near.state
        prop.district = near.district


async def guess_point_from_text(db: AsyncSession, prop: Property) -> bool:
    """Best-effort point for a property that has none (e.g. a fresh Airbnb
    import): look the property's city, then a "near X" in its name, up in the
    gazetteer. Only accepts an UNAMBIGUOUS answer -- exactly one place with
    that name, or one populated town -- because a wrong point is worse than
    none (village names repeat all over India; a host pin or website
    coordinate is the fix for those). Returns True when a point was set."""
    terms: list[str] = []
    if prop.city:
        terms.append(prop.city)
    for text in (prop.display_name, prop.name):
        match = _NEAR_IN_NAME.search(text or "")
        if match:
            terms.append(match.group(1))
    for term in terms:
        name = strip_filler(normalize(term))
        if not name:
            continue
        candidates = await _exact_candidates(db, name, None)
        towns = [c for c in candidates if c.population >= TOWN_POPULATION]
        chosen = candidates[0] if len(candidates) == 1 else (towns[0] if len(towns) == 1 else None)
        if chosen is not None:
            await set_property_point(db, prop, chosen.lat, chosen.lng, "gazetteer")
            return True
    return False


async def ensure_property_location(db: AsyncSession, prop: Property) -> bool:
    """Import hook: give a property with no point a conservative guess, and
    never let that get in the way of the import. Commits on success; on any
    error rolls back and carries on (the property just stays unlocated, still
    found by the free-text match). Always leaves `prop` refreshed so the caller
    can return it as-is. True when a point was set."""
    if prop.latitude is not None:
        return False
    # Read before any rollback: a rollback expires the instance, and touching an
    # expired attribute in async code is a lazy load that raises.
    property_id = prop.id
    located = False
    try:
        located = await guess_point_from_text(db, prop)
        if located:
            await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("could not guess a location for property %s; leaving it unlocated", property_id)
        located = False
    await db.refresh(prop)
    return located
