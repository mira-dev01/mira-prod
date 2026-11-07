"""Location search by map position: guest names an area, the host's properties
near it are found even when no property's text mentions that area.

The regression these exist for (confirmed live 2026-11-07): a guest asked for
"Himachal"; the host's Manali villa is stored under the village "Pangan" and
the Kasol cottage under "Jari", neither text ever says "Himachal", so the
search returned nothing and the model handed a guest it had just quoted
villas to over to the host.
"""

import pytest
import pytest_asyncio

from app.models.place import AdminRegion, Place, PlaceAlias
from app.models.property import Property
from app.schemas.tool import RecommendPropertiesArgs
from app.services.geo import search as geo_search
from app.services.geo.distance import haversine_km
from app.services.geo.gazetteer import nearest_place, resolve_location
from app.database import AsyncSessionLocal
from app.services.geo import property_location
from app.services.geo.property_location import ensure_property_location, guess_point_from_text, set_property_point
from app.services.geo.states import match_state
from app.services.geo.text import normalize, skeleton, strip_filler
from app.services.property.pitch_formatter import render_recommendation_text
from app.services.property.retrieval import orchestrator

# Real coordinates. (Manali to Kasol is ~28 km straight-line, ~75 km by road.)
MANALI = (32.2432, 77.1892)
PANGAN = (32.1597, 77.1604)  # Hunzuru villa, ~10 km from Manali
KASOL = (32.0099, 77.3142)
JARI_HP = (31.9965, 77.2441)  # the Slow-life cottage, ~7 km from Kasol
JARI_UP = (25.5175, 80.4573)  # a different village also called Jari
NAINITAL = (29.3919, 79.4542)
GOA = (15.4909, 73.8278)
SHIMLA = (31.1048, 77.1734)  # ~128 km from Manali


def _place(geoname_id, name, latlng, admin1="IN.11", admin2="IN.11.026", population=0):
    return Place(
        geoname_id=geoname_id, name=name, name_norm=normalize(name), name_key=skeleton(name),
        lat=latlng[0], lng=latlng[1], admin1_code=admin1, admin2_code=admin2, population=population,
    )


@pytest_asyncio.fixture
async def gazetteer(db_session):
    db_session.add_all(
        [
            AdminRegion(code="IN.11", level=1, name="Himachal Pradesh"),
            AdminRegion(code="IN.11.026", level=2, name="Kullu"),
            AdminRegion(code="IN.39", level=1, name="Uttarakhand"),
            AdminRegion(code="IN.39.060", level=2, name="Nainital"),
            AdminRegion(code="IN.33", level=1, name="Goa"),
            AdminRegion(code="IN.36", level=1, name="Uttar Pradesh"),
        ]
    )
    await db_session.flush()
    db_session.add_all(
        [
            _place(1, "Manali", MANALI, population=8096),
            _place(2, "Kasol", KASOL),
            _place(3, "Jari", JARI_HP),
            _place(4, "Jari", JARI_UP, admin1="IN.36", admin2=None),
            _place(5, "Naini Tal", NAINITAL, admin1="IN.39", admin2="IN.39.060", population=41377),
            _place(6, "Panaji", GOA, admin1="IN.33", admin2=None, population=114759),
            _place(7, "Pangan", PANGAN),
        ]
    )
    await db_session.flush()
    db_session.add(PlaceAlias(place_id=5, alias_norm="nainital", alias_key=skeleton("nainital")))
    await db_session.commit()


async def _property(db, user, name, latlng=None, state=None, city="", price=10000, guests=10, exophone="+918011100001"):
    prop = Property(
        user_id=user.id, name=name, city=city, base_price=price, max_guests=guests, exophone=exophone,
        latitude=latlng[0] if latlng else None, longitude=latlng[1] if latlng else None, state=state,
    )
    db.add(prop)
    await db.commit()
    await db.refresh(prop)
    return prop


# ---------------------------------------------------------------- pure helpers


def test_normalize_and_filler():
    assert normalize("  Naini-Tal, Uttarakhand ") == "naini tal uttarakhand"
    assert normalize("J&K") == "j and k"
    assert strip_filler("somewhere around manali") == "manali"
    assert strip_filler("near to kasol") == "kasol"
    assert strip_filler("kasol") == "kasol"


def test_skeleton_tolerates_speech_to_text_spelling():
    assert skeleton("Kasol") == skeleton("Kasaul") == skeleton("Kassol")
    assert skeleton("Naini Tal") == skeleton("Nainital")
    assert skeleton("Kullu") == skeleton("Kulu")
    assert skeleton("Manali") != skeleton("Kasol")


def test_match_state():
    assert match_state("Himachal") == "Himachal Pradesh"
    assert match_state("himachal pradesh state") == "Himachal Pradesh"
    assert match_state("Kashmir") == "Jammu and Kashmir"
    assert match_state("Goa") == "Goa"
    assert match_state("Manali") is None
    assert match_state("up") is None  # an ordinary word, deliberately not an alias


def test_haversine_known_distance():
    # ~28 km straight line (about 75 km by road -- why distances are always said as straight-line).
    assert 25 < haversine_km(*MANALI, *KASOL) < 32
    assert haversine_km(*MANALI, *MANALI) == 0


# ---------------------------------------------------------------- resolver


async def test_state_phrase_resolves_to_state(db_session, gazetteer):
    loc = await resolve_location(db_session, "Himachal", [])
    assert loc is not None and loc.kind == "state" and loc.state == "Himachal Pradesh"


async def test_ambiguous_name_resolves_to_the_one_near_the_hosts_property(db_session, gazetteer):
    # 'Jari' exists in HP and in UP; the host has a villa near the HP one.
    loc = await resolve_location(db_session, "Jari", [PANGAN, JARI_HP])
    assert loc.kind == "place" and abs(loc.lat - JARI_HP[0]) < 0.01
    assert loc.state == "Himachal Pradesh"


async def test_ambiguous_name_with_nothing_nearby_prefers_the_biggest(db_session, gazetteer):
    # A host with only a Nainital villa: neither Jari is near, so no portfolio bias applies
    # (both populations are 0 -> deterministic, just must not crash and must resolve).
    loc = await resolve_location(db_session, "Jari", [NAINITAL])
    assert loc is not None and loc.kind == "place"


async def test_alias_matches_gazetteer_spelling_difference(db_session, gazetteer):
    loc = await resolve_location(db_session, "Nainital", [NAINITAL])
    # Said the way the guest said it, not GeoNames' "Naini Tal".
    assert loc.label == "Nainital" and abs(loc.lat - NAINITAL[0]) < 0.01


async def test_fuzzy_spelling_resolves_only_when_near_a_property(db_session, gazetteer):
    near = await resolve_location(db_session, "Kasaul", [PANGAN, JARI_HP])
    assert near is not None and near.label == "Kasol"
    # Same mangled word, host only has a Goa villa: must NOT resolve to a far-away village.
    assert await resolve_location(db_session, "Kasaul", [GOA]) is None


async def test_a_real_town_beats_a_nearby_hamlet_of_the_same_name(db_session, test_user):
    """Found on real data: 'Jaipur' resolved to a hamlet in Uttarakhand that was
    closer to one of the host's villas than the actual city was."""
    jaipur_city, jaipur_hamlet = (26.9124, 75.7873), (29.45, 79.55)
    db_session.add_all(
        [
            AdminRegion(code="IN.24", level=1, name="Rajasthan"),
            _place(10, "Jaipur", jaipur_city, admin1="IN.24", admin2=None, population=3046163),
            _place(11, "Jaipur", jaipur_hamlet, admin1="IN.39", admin2=None, population=0),
        ]
    )
    await db_session.commit()
    anchors = [(29.4718, 79.6175), (27.0836, 75.8971)]  # a Nainital-area villa and a Kukas villa
    loc = await resolve_location(db_session, "Jaipur", anchors)
    assert abs(loc.lat - jaipur_city[0]) < 0.01


async def test_fuzzy_match_is_labelled_with_the_real_name(db_session, gazetteer):
    loc = await resolve_location(db_session, "Kasaul", [PANGAN, JARI_HP])
    assert loc.label == "Kasol"


async def test_state_hint_narrows_ambiguous_place(db_session, gazetteer):
    loc = await resolve_location(db_session, "Jari, Uttar Pradesh", [JARI_HP])
    assert abs(loc.lat - JARI_UP[0]) < 0.01


async def test_unknown_place_returns_none(db_session, gazetteer):
    assert await resolve_location(db_session, "Atlantis", [PANGAN]) is None
    assert await resolve_location(db_session, "", [PANGAN]) is None


# ---------------------------------------------------------------- property matching


async def test_match_by_place_returns_nearby_properties_with_distances(db_session, test_user, gazetteer):
    hunzuru = await _property(db_session, test_user, "Hunzuru", PANGAN, exophone="+918011100001")
    slowlife = await _property(db_session, test_user, "Slow-life", JARI_HP, exophone="+918011100002")
    far = await _property(db_session, test_user, "Woodhouse", NAINITAL, exophone="+918011100003")

    match = await geo_search.match_properties(db_session, test_user.id, "Kasol")
    assert match.property_ids == {hunzuru.id, slowlife.id}
    assert far.id not in match.property_ids
    assert match.distances_km[slowlife.id] < match.distances_km[hunzuru.id]
    assert not match.farther


async def test_match_falls_back_to_farther_ring_and_says_so(db_session, test_user, gazetteer):
    shimla_villa = await _property(db_session, test_user, "Shimla Villa", SHIMLA)
    match = await geo_search.match_properties(db_session, test_user.id, "Manali")
    assert match.farther is True
    assert match.property_ids == {shimla_villa.id}
    assert 120 < match.distances_km[shimla_villa.id] < 140


async def test_match_finds_nothing_beyond_the_farther_ring(db_session, test_user, gazetteer):
    await _property(db_session, test_user, "Woodhouse", NAINITAL)  # ~400 km from Manali
    match = await geo_search.match_properties(db_session, test_user.id, "Manali")
    assert match is not None  # the place was understood...
    assert match.property_ids == set() and not match.farther  # ...but nothing is near it


async def test_match_by_state_uses_stored_state(db_session, test_user, gazetteer):
    himachal = await _property(db_session, test_user, "Hunzuru", PANGAN, state="Himachal Pradesh")
    await _property(db_session, test_user, "Woodhouse", NAINITAL, state="Uttarakhand", exophone="+918011100002")
    match = await geo_search.match_properties(db_session, test_user.id, "Himachal")
    assert match.property_ids == {himachal.id}


async def test_no_location_data_means_no_geo_match(db_session, test_user, gazetteer):
    await _property(db_session, test_user, "Plain", None)
    assert await geo_search.match_properties(db_session, test_user.id, "Himachal") is None


async def test_list_areas_orders_by_property_count(db_session, test_user, gazetteer):
    await _property(db_session, test_user, "A", PANGAN, state="Himachal Pradesh", exophone="+918011100001")
    await _property(db_session, test_user, "B", JARI_HP, state="Himachal Pradesh", exophone="+918011100002")
    await _property(db_session, test_user, "C", NAINITAL, state="Uttarakhand", exophone="+918011100003")
    assert await geo_search.list_areas(db_session, test_user.id) == ["Himachal Pradesh", "Uttarakhand"]


# ---------------------------------------------------------------- recommend_properties end to end


async def test_himachal_query_finds_villas_whose_text_never_says_himachal(db_session, test_user, gazetteer):
    """The exact failure from the live call."""
    await _property(db_session, test_user, "Hunzuru Villa", PANGAN, state="Himachal Pradesh", city="Pangan",
                    price=30000, exophone="+918011100001")
    await _property(db_session, test_user, "Slow-life Cottage", JARI_HP, state="Himachal Pradesh", city="Jari",
                    price=35000, exophone="+918011100002")
    await _property(db_session, test_user, "Woodhouse", NAINITAL, state="Uttarakhand", city="Nainital",
                    exophone="+918011100003")
    args = RecommendPropertiesArgs(preferred_location="Himachal", num_guests=5, nights=5)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert not result.not_found
    assert {c.spoken_name for c in result.options} == {"Hunzuru Villa", "Slow-life Cottage"}


async def test_place_query_orders_nearest_first_and_states_distance(db_session, test_user, gazetteer):
    await _property(db_session, test_user, "Hunzuru Villa", PANGAN, state="Himachal Pradesh", price=5000,
                    exophone="+918011100001")
    await _property(db_session, test_user, "Slow-life Cottage", JARI_HP, state="Himachal Pradesh", price=9000,
                    exophone="+918011100002")
    args = RecommendPropertiesArgs(preferred_location="near Kasol", num_guests=4)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    # The cheaper villa is further from Kasol; distance wins over price.
    assert [c.spoken_name for c in result.options] == ["Slow-life Cottage", "Hunzuru Villa"]
    assert "from Kasol in a straight line" in result.options[0].match_reasons[0]
    text = render_recommendation_text(result)
    assert "km from Kasol" in text


async def test_missing_area_offers_real_alternatives_instead_of_handing_off(db_session, test_user, gazetteer):
    await _property(db_session, test_user, "Hunzuru Villa", PANGAN, state="Himachal Pradesh", exophone="+918011100001")
    await _property(db_session, test_user, "Woodhouse", NAINITAL, state="Uttarakhand", exophone="+918011100002")
    args = RecommendPropertiesArgs(preferred_location="Goa")
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert result.not_found
    assert result.missing_location == "Goa"
    assert result.available_areas == ["Himachal Pradesh", "Uttarakhand"]
    text = render_recommendation_text(result)
    assert "We don't have a property in or near Goa" in text
    assert "Himachal Pradesh" in text and "do NOT hand the guest to the host" in text


async def test_description_that_merely_mentions_the_area_does_not_pull_in_a_distant_property(
    db_session, test_user, gazetteer
):
    """Found on real data: 'Ooty' also returned a Dehradun villa whose description
    says 'like Ooty'. A located property is judged by where it is."""
    near = await _property(db_session, test_user, "Hunzuru", PANGAN, state="Himachal Pradesh", exophone="+918011100001")
    distant = await _property(db_session, test_user, "Woodhouse", NAINITAL, state="Uttarakhand", exophone="+918011100002")
    distant.neighborhood_info = "A quiet hill stay, a bit like Manali in the 90s."
    await db_session.commit()
    args = RecommendPropertiesArgs(preferred_location="Manali", num_guests=4)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert [c.spoken_name for c in result.options] == ["Hunzuru"]
    assert near.id != distant.id


async def test_unlocated_property_is_still_found_by_text_beside_located_ones(db_session, test_user, gazetteer):
    await _property(db_session, test_user, "Hunzuru", PANGAN, state="Himachal Pradesh", exophone="+918011100001")
    await _property(db_session, test_user, "Old Style", None, city="Manali", exophone="+918011100002")
    args = RecommendPropertiesArgs(preferred_location="Manali", num_guests=4)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert {c.spoken_name for c in result.options} == {"Hunzuru", "Old Style"}


async def test_text_match_still_works_for_a_property_with_no_location_data(db_session, test_user, gazetteer):
    await _property(db_session, test_user, "Old Style", None, city="Alleppey", exophone="+918011100001")
    args = RecommendPropertiesArgs(preferred_location="Alleppey")
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert [c.spoken_name for c in result.options] == ["Old Style"]


async def test_location_lookup_failure_falls_back_to_text_match(db_session, test_user, gazetteer, monkeypatch):
    await _property(db_session, test_user, "Old Style", None, city="Alleppey", exophone="+918011100001")

    async def boom(*a, **k):
        raise RuntimeError("gazetteer down")

    monkeypatch.setattr(geo_search, "match_properties", boom)
    args = RecommendPropertiesArgs(preferred_location="Alleppey")
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert [c.spoken_name for c in result.options] == ["Old Style"]


# ---------------------------------------------------------------- writing locations


async def test_set_property_point_derives_state_and_district(db_session, test_user, gazetteer):
    prop = await _property(db_session, test_user, "Hunzuru", None, city="Pangan")
    await set_property_point(db_session, prop, 32.1597073, 77.1604175, "host_website")
    assert prop.state == "Himachal Pradesh" and prop.district == "Kullu"
    assert prop.location_source == "host_website"
    assert float(prop.latitude) == pytest.approx(32.159707, abs=1e-6)


async def test_nearest_place_ignores_places_too_far_away(db_session, gazetteer):
    assert await nearest_place(db_session, 11.4, 76.9) is None  # Kotagiri: nothing in the test gazetteer


async def test_guess_point_accepts_only_unambiguous_names(db_session, test_user, gazetteer):
    unique = await _property(db_session, test_user, "Hunzuru", None, city="Pangan", exophone="+918011100001")
    assert await guess_point_from_text(db_session, unique) is True
    assert unique.location_source == "gazetteer" and unique.state == "Himachal Pradesh"

    ambiguous = await _property(db_session, test_user, "Slow-life", None, city="Jari", exophone="+918011100002")
    assert await guess_point_from_text(db_session, ambiguous) is False
    assert ambiguous.latitude is None  # a wrong point is worse than none

    town = await _property(db_session, test_user, "Villa", None, city="Manali", exophone="+918011100003")
    assert await guess_point_from_text(db_session, town) is True


async def test_import_hook_sets_and_persists_an_unambiguous_point(db_session, test_user, gazetteer):
    prop = await _property(db_session, test_user, "Hunzuru", None, city="Pangan")
    assert await ensure_property_location(db_session, prop) is True
    assert prop.state == "Himachal Pradesh"
    async with AsyncSessionLocal() as other:  # committed, not just set on the in-memory object
        stored = await other.get(Property, prop.id)
        assert stored.latitude is not None and stored.location_source == "gazetteer"


async def test_import_hook_never_breaks_the_import(db_session, test_user, gazetteer, monkeypatch):
    prop = await _property(db_session, test_user, "Hunzuru", None, city="Pangan")

    async def boom(*a, **k):
        raise RuntimeError("gazetteer exploded")

    monkeypatch.setattr(property_location, "guess_point_from_text", boom)
    assert await ensure_property_location(db_session, prop) is False
    assert prop.name == "Hunzuru" and prop.latitude is None  # still usable by the caller


async def test_import_hook_leaves_an_existing_point_alone(db_session, test_user, gazetteer):
    prop = await _property(db_session, test_user, "Hunzuru", KASOL, city="Pangan")
    assert await ensure_property_location(db_session, prop) is False
    assert float(prop.latitude) == pytest.approx(KASOL[0], abs=1e-5)
