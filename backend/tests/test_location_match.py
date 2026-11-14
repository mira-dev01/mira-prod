"""Resolving a guest's words against the host's own areas (Task 12).

The phrases below are the real ones from test call 56947e99 (2026-11-13) plus the spellings speech-to-text
produces for the same place. The rule they pin: a known area resolves however it is spelled; an unknown word
never resolves to a place the host does not have.
"""

import pytest

from app.services.property.retrieval.location_match import HostPlaces, load_host_places, resolve, same_area

LOCALITIES = [("Indiranagar", "Bengaluru"), ("Koramangala", "Bengaluru"), ("HSR Layout", "Bengaluru"), ("Madhapur", "Hyderabad")]
CITIES = ["Bengaluru", "Hyderabad", "Goa"]


@pytest.mark.parametrize(
    "phrase",
    ["Indiranagar", "Indira Nagar", "indiranagar ", "near Indiranagar", "Indiranagar Bangalore", "in Indira nagar, Bengaluru"],
)
def test_every_spelling_of_a_known_area_resolves_to_it(phrase):
    match = resolve(phrase, LOCALITIES, CITIES)
    assert (match.locality, match.city) == ("Indiranagar", "Bengaluru")
    assert match.via == "locality"


@pytest.mark.parametrize("phrase", ["Indra Nagar", "Indranagar", "Kormangala", "Koramangla"])
def test_a_misheard_spelling_resolves_through_the_skeleton_but_only_to_this_hosts_areas(phrase):
    match = resolve(phrase, LOCALITIES, CITIES)
    assert match.via == "locality_skeleton"
    assert match.locality in {"Indiranagar", "Koramangala"}


def test_a_multi_word_area_resolves():
    assert resolve("hsr layout", LOCALITIES, CITIES).locality == "HSR Layout"
    assert resolve("H S R Layout", LOCALITIES, CITIES).locality == "HSR Layout"


@pytest.mark.parametrize("phrase", ["Juhu", "Juhu Bangalore", "Bandra", "Jayanagar", "Whitefield"])
def test_an_area_the_host_does_not_have_never_resolves_even_next_to_a_city_it_has(phrase):
    match = resolve(phrase, LOCALITIES, CITIES)
    assert match.via == "none"
    assert match.locality is None


def test_a_city_alone_resolves_to_the_city_in_either_spelling():
    assert resolve("Bangalore", LOCALITIES, CITIES).city == "Bengaluru"
    assert resolve("Bengaluru", LOCALITIES, CITIES).via == "city"
    assert resolve("Hyderabad", LOCALITIES, CITIES).city == "Hyderabad"


def test_a_city_the_host_does_not_have_does_not_resolve():
    assert resolve("Delhi", LOCALITIES, CITIES).via == "none"


def test_a_region_phrase_is_left_to_the_old_text_match():
    # "North Goa" is handled by filter_builder's own locality list, not here.
    assert resolve("North Goa", LOCALITIES, CITIES).via == "none"


def test_the_same_area_name_in_two_cities_prefers_the_city_the_guest_named():
    localities = [("Jayanagar", "Bengaluru"), ("Jayanagar", "Mysuru")]
    match = resolve("Jayanagar Mysore", localities, ["Bengaluru", "Mysuru"])
    # "Mysore" is not the stored spelling, so it is no city hint; falls back to a stable pick.
    assert match.locality == "Jayanagar"
    named = resolve("Jayanagar Bangalore", localities, ["Bengaluru", "Mysuru"])
    assert named.city == "Bengaluru"


@pytest.mark.parametrize("text", [None, "", "   ", "इंदिरानगर"])
def test_nothing_to_match_resolves_to_nothing(text):
    assert resolve(text, LOCALITIES, CITIES).via == "none"


def test_no_localities_set_means_only_cities_can_resolve():
    assert resolve("Indiranagar", [], CITIES).via == "none"
    assert resolve("Hyderabad", [], CITIES).via == "city"


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("Indira Nagar", "Indiranagar", True),
        ("Indra Nagar", "Indiranagar", True),
        ("Goa", "North Goa", True),
        ("Koramangala", "Indiranagar", False),
        ("Juhu", "Indiranagar", False),
        ("", "Indiranagar", False),
        (None, None, False),
    ],
)
def test_same_area(a, b, same):
    assert same_area(a, b) is same


@pytest.mark.asyncio
async def test_host_places_are_loaded_from_the_hosts_own_properties_only(db_session, test_user):
    from app.models.property import Property
    from app.models.user import User

    other = User(email="someone-else@example.com", name="Other")
    db_session.add(other)
    await db_session.flush()
    db_session.add_all(
        [
            Property(user_id=test_user.id, name="A", city="Bengaluru", locality="Indiranagar", locality_key="indiranagar", base_price=1),
            Property(user_id=test_user.id, name="B", city="Bengaluru", locality="Indiranagar", locality_key="indiranagar", base_price=1),
            Property(user_id=test_user.id, name="C", city="Goa", base_price=1),
            Property(user_id=other.id, name="D", city="Mumbai", locality="Juhu", locality_key="juhu", base_price=1),
        ]
    )
    await db_session.commit()

    places = await load_host_places(db_session, test_user.id)

    assert places == HostPlaces(localities=(("Indiranagar", "Bengaluru"),), cities=("Bengaluru", "Goa"))


# --- found in the Phase 3 recheck ---

MUMBAI_HOST = [("Bandra West", "Mumbai"), ("Andheri West", "Mumbai"), ("HSR Layout", "Bengaluru"), ("BTM Layout", "Bengaluru")]
MUMBAI_CITIES = ["Mumbai", "Bengaluru"]


def test_the_first_word_of_an_area_resolves_when_only_one_area_starts_that_way():
    for phrase, expected in (("Bandra", "Bandra West"), ("bandra", "Bandra West"), ("HSR", "HSR Layout")):
        match = resolve(phrase, MUMBAI_HOST, MUMBAI_CITIES)
        assert (match.locality, match.via) == (expected, "locality_prefix"), phrase


@pytest.mark.parametrize("phrase", ["West", "Layout", "Wes"])
def test_a_middle_or_last_word_never_names_an_area(phrase):
    assert resolve(phrase, MUMBAI_HOST, MUMBAI_CITIES).via == "none"


def test_a_first_word_shared_by_two_areas_resolves_to_neither():
    host = MUMBAI_HOST + [("Bandra East", "Mumbai")]
    assert resolve("Bandra", host, MUMBAI_CITIES).via == "none"


def test_an_exact_area_still_beats_the_prefix_reading():
    host = MUMBAI_HOST + [("Bandra", "Mumbai")]
    match = resolve("Bandra", host, MUMBAI_CITIES)
    assert (match.locality, match.via) == ("Bandra", "locality")


def test_an_area_in_a_different_city_than_the_one_named_does_not_resolve():
    assert resolve("Indiranagar Mumbai", LOCALITIES + [("Bandra West", "Mumbai")], CITIES + ["Mumbai"]).via == "none"
    # ...but the right city, or none, still does.
    assert resolve("Indiranagar Bangalore", LOCALITIES, CITIES).locality == "Indiranagar"
    assert resolve("Indiranagar", LOCALITIES, CITIES).locality == "Indiranagar"


def test_an_area_in_a_city_the_host_does_not_have_is_not_blocked_by_the_city_rule():
    # "Delhi" is not one of the host's cities, so it is just an unknown word beside a known area.
    assert resolve("Indiranagar Delhi", LOCALITIES, CITIES).locality == "Indiranagar"
