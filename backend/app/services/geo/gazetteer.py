"""Lookups against the local gazetteer (app/models/place.py): what point does
the area a guest named correspond to, and which state/district is a point in.

Name matching is exact first, then a spelling-tolerant consonant skeleton for
what speech-to-text mangles. The same name exists in many places in India (14
villages are called "Jari"), so a name is never resolved by text alone: among
the candidates, the one near the host's own properties wins (guests ask about
places the host actually has villas near), and with nothing near the host the
most populous wins. A fuzzy-only match must be near a property to count --
a mis-heard word must not resolve to a random village across the country.
"""

from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.place import AdminRegion, Place, PlaceAlias
from app.services.geo.distance import haversine_km
from app.services.geo.states import match_state
from app.services.geo.text import normalize, skeleton, strip_filler

# "Near" a property: straight-line, so deliberately generous for hill country
# where a 45 km hop can be a 2-hour drive. Callers never present it as drive time.
NEAR_RADIUS_KM = 50.0
# A real town/city, as opposed to a hamlet that merely shares its name.
TOWN_POPULATION = 5000

_MAX_CANDIDATES = 500
_FUZZY_BOX_DEG = 0.6  # ~66 km, a little over NEAR_RADIUS_KM
_MIN_FUZZY_KEY_LEN = 3


@dataclass(frozen=True)
class ResolvedLocation:
    kind: str  # "state" | "place"
    label: str  # what to call it aloud: "Kasol", "Himachal Pradesh"
    state: str | None = None
    lat: float | None = None
    lng: float | None = None


@dataclass(frozen=True)
class NearestPlace:
    name: str
    state: str | None
    district: str | None
    distance_km: float


def _min_distance_km(place: Place, anchors: list[tuple[float, float]]) -> float | None:
    if not anchors:
        return None
    return min(haversine_km(place.lat, place.lng, a_lat, a_lng) for a_lat, a_lng in anchors)


async def _state_code(db: AsyncSession, state: str) -> str | None:
    return await db.scalar(select(AdminRegion.code).where(AdminRegion.level == 1, AdminRegion.name == state))


async def _exact_candidates(db: AsyncSession, name_norm: str, admin1_code: str | None) -> list[Place]:
    stmt = select(Place).where(
        or_(
            Place.name_norm == name_norm,
            Place.geoname_id.in_(select(PlaceAlias.place_id).where(PlaceAlias.alias_norm == name_norm)),
        )
    )
    if admin1_code:
        stmt = stmt.where(Place.admin1_code == admin1_code)
    return list((await db.scalars(stmt.limit(_MAX_CANDIDATES))).all())


async def _fuzzy_candidates(
    db: AsyncSession, key: str, admin1_code: str | None, anchors: list[tuple[float, float]]
) -> list[Place]:
    # Only places near one of the host's properties: bounds the candidate set
    # (a skeleton is shared by many names) and is the rule for accepting a fuzzy match anyway.
    cells = {(round(lat, 1), round(lng, 1)) for lat, lng in anchors}
    boxes = [
        and_(
            Place.lat.between(lat - _FUZZY_BOX_DEG, lat + _FUZZY_BOX_DEG),
            Place.lng.between(lng - _FUZZY_BOX_DEG, lng + _FUZZY_BOX_DEG),
        )
        for lat, lng in sorted(cells)[:40]
    ]
    stmt = select(Place).where(
        or_(Place.name_key == key, Place.geoname_id.in_(select(PlaceAlias.place_id).where(PlaceAlias.alias_key == key))),
        or_(*boxes),
    )
    if admin1_code:
        stmt = stmt.where(Place.admin1_code == admin1_code)
    return list((await db.scalars(stmt.limit(_MAX_CANDIDATES))).all())


def _pick(candidates: list[Place], anchors: list[tuple[float, float]], fuzzy: bool) -> Place | None:
    scored = [(c, _min_distance_km(c, anchors)) for c in candidates]
    near = [(c, d) for c, d in scored if d is not None and d <= NEAR_RADIUS_KM]
    if near:
        # A real town beats a hamlet of the same name that happens to sit closer to
        # one of the host's properties ("Jaipur" is the city, not a village in Uttarakhand).
        towns = [(c, d) for c, d in near if c.population >= TOWN_POPULATION]
        if towns:
            return max(towns, key=lambda cd: (cd[0].population, -cd[1]))[0]
        return min(near, key=lambda cd: (cd[1], -cd[0].population))[0]
    if fuzzy:
        return None
    return max(scored, key=lambda cd: (cd[0].population, -(cd[1] if cd[1] is not None else 1e9)))[0]


async def resolve_location(
    db: AsyncSession, phrase: str | None, anchors: list[tuple[float, float]]
) -> ResolvedLocation | None:
    """`phrase` is what the guest said ("Kasol", "near Manali", "Kasol, Himachal",
    "Kerala"). `anchors` are the host's property coordinates. None = not found."""
    if not phrase or not phrase.strip():
        return None
    state_hint: str | None = None
    place_part: str | None = None
    for raw in phrase.split(","):
        part = strip_filler(normalize(raw))
        if not part:
            continue
        state = match_state(part)
        if state:
            state_hint = state_hint or state
        elif place_part is None:
            place_part = part
    if place_part is None:
        return ResolvedLocation("state", state_hint, state=state_hint) if state_hint else None

    admin1_code = await _state_code(db, state_hint) if state_hint else None
    candidates = await _exact_candidates(db, place_part, admin1_code)
    fuzzy = False
    if not candidates:
        key = skeleton(place_part)
        if len(key) >= _MIN_FUZZY_KEY_LEN and anchors:
            candidates = await _fuzzy_candidates(db, key, admin1_code, anchors)
            fuzzy = True
    if not candidates:
        return None
    best = _pick(candidates, anchors, fuzzy)
    if best is None:
        return None
    state_name = await db.scalar(select(AdminRegion.name).where(AdminRegion.code == best.admin1_code)) if best.admin1_code else None
    # Say the place the way the guest did ("Nainital", not GeoNames' "Naini Tal") --
    # unless their word was a mangled spelling, then the real name.
    label = best.name if fuzzy else place_part.title()
    return ResolvedLocation("place", label, state=state_name or state_hint, lat=best.lat, lng=best.lng)


async def nearest_place(db: AsyncSession, lat: float, lng: float, max_km: float = 40.0) -> NearestPlace | None:
    """The closest gazetteer place to a point, with its state and district --
    how a property's state/district is derived from its coordinates."""
    box = max_km / 111.0
    rows = list(
        (
            await db.scalars(
                select(Place)
                .where(Place.lat.between(lat - box, lat + box), Place.lng.between(lng - box * 1.3, lng + box * 1.3))
                .limit(5000)
            )
        ).all()
    )
    if not rows:
        return None
    best = min(rows, key=lambda p: haversine_km(lat, lng, p.lat, p.lng))
    distance = haversine_km(lat, lng, best.lat, best.lng)
    if distance > max_km:
        return None
    codes = [c for c in (best.admin1_code, best.admin2_code) if c]
    names = dict((await db.execute(select(AdminRegion.code, AdminRegion.name).where(AdminRegion.code.in_(codes)))).all()) if codes else {}
    return NearestPlace(
        name=best.name,
        state=names.get(best.admin1_code),
        district=names.get(best.admin2_code),
        distance_km=distance,
    )
