from app.models.property import Property
from app.schemas.tool import RecommendPropertiesArgs
from app.services.property.retrieval.filter_builder import (
    apply_amenity_boost,
    apply_landmark_boost,
    apply_premium_boost,
    matches_landmark,
)


def _property(**overrides) -> Property:
    import uuid

    defaults = dict(id=uuid.uuid4(), user_id=uuid.uuid4(), name="Test", base_price=3000, max_guests=2)
    defaults.update(overrides)
    return Property(**defaults)


def test_matches_landmark_exact_name():
    property_ = _property(landmarks=[{"name": "Thalassa", "distance_minutes": 5, "mode": "walk"}])
    assert matches_landmark(property_, "Thalassa") is True


def test_matches_landmark_fuzzy_typo():
    property_ = _property(landmarks=[{"name": "Thalassa", "distance_minutes": 5}])
    assert matches_landmark(property_, "Thalasa") is True


def test_matches_landmark_no_match_returns_false():
    property_ = _property(landmarks=[{"name": "Thalassa", "distance_minutes": 5}])
    assert matches_landmark(property_, "Completely Unrelated Place") is False


def test_matches_landmark_falls_back_to_neighborhood_info_when_no_structured_data():
    property_ = _property(landmarks=[], neighborhood_info="Two minutes from Thalassa beach club.")
    assert matches_landmark(property_, "Thalassa") is True


def test_matches_landmark_empty_query_never_matches():
    property_ = _property(landmarks=[{"name": "Thalassa", "distance_minutes": 5}])
    assert matches_landmark(property_, "") is False


def test_apply_landmark_boost_moves_matching_property_first_without_dropping_others():
    near = _property(name="Near", landmarks=[{"name": "Thalassa", "distance_minutes": 5}])
    far = _property(name="Far")
    boosted = apply_landmark_boost([far, near], "Thalassa")
    assert [p.name for p in boosted] == ["Near", "Far"]


def test_apply_landmark_boost_no_query_is_a_no_op():
    a = _property(name="A")
    b = _property(name="B")
    assert apply_landmark_boost([a, b], None) == [a, b]


def test_apply_landmark_boost_never_drops_properties_when_nothing_matches():
    a = _property(name="A")
    b = _property(name="B")
    result = apply_landmark_boost([a, b], "Nonexistent Place")
    assert len(result) == 2


def test_apply_premium_boost_moves_premium_property_first_without_dropping_others():
    """Recommendation conversations ("Phase X"): "something more premium"
    ranks Property.is_premium=True first -- a host-set fact, never an
    LLM-guessed judgment of which property "feels" nicer."""
    regular = _property(name="Regular", is_premium=False)
    premium = _property(name="Premium", is_premium=True)
    boosted = apply_premium_boost([regular, premium], more_premium_than_shown=True)
    assert [p.name for p in boosted] == ["Premium", "Regular"]


def test_apply_premium_boost_flag_false_is_a_no_op():
    a = _property(name="A", is_premium=False)
    b = _property(name="B", is_premium=True)
    assert apply_premium_boost([a, b], more_premium_than_shown=False) == [a, b]


def test_apply_premium_boost_never_drops_properties_when_host_has_none_set():
    """A host with zero is_premium properties (the default, since it's
    opt-in) must still get a normal, non-empty result -- never nothing,
    just no reordering."""
    a = _property(name="A", is_premium=False)
    b = _property(name="B", is_premium=False)
    result = apply_premium_boost([a, b], more_premium_than_shown=True)
    assert len(result) == 2


def test_apply_amenity_boost_ranks_by_how_many_requested_amenities_match():
    """Recommendation conversations ("Phase X"): required_amenities is now
    a SOFT ranking preference (deliberately no longer a hard WHERE filter --
    see build_base_filters), ranked by count of matching amenity_tags, most
    matches first -- never excludes a property for lacking one."""
    none_match = _property(name="None", amenity_tags=[])
    one_match = _property(name="One", amenity_tags=["pool"])
    both_match = _property(name="Both", amenity_tags=["pool", "pets_allowed"])
    boosted = apply_amenity_boost([none_match, one_match, both_match], ["pool", "pet friendly"])
    assert [p.name for p in boosted] == ["Both", "One", "None"]


def test_apply_amenity_boost_no_amenities_requested_is_a_no_op():
    a = _property(name="A", amenity_tags=[])
    b = _property(name="B", amenity_tags=["pool"])
    assert apply_amenity_boost([a, b], None) == [a, b]
    assert apply_amenity_boost([a, b], []) == [a, b]


def test_apply_amenity_boost_never_drops_a_property_missing_every_requested_amenity():
    """The actual behavior change from the old hard filter: a property
    matching NONE of the requested amenities is still returned, just
    ranked last -- never excluded to zero results."""
    no_match = _property(name="NoMatch", amenity_tags=["wifi"])
    match = _property(name="Match", amenity_tags=["pool"])
    result = apply_amenity_boost([no_match, match], ["pool"])
    assert len(result) == 2
    assert {p.name for p in result} == {"NoMatch", "Match"}


def test_apply_amenity_boost_canonicalizes_synonyms():
    """"pet friendly" (guest phrasing) must match a property tagged
    "pets_allowed" (the canonical amenity_tags form) -- same
    canonicalize_amenities normalization already used at import time and by
    match_reasons_for_card, not a second, separate comparison."""
    property_ = _property(name="PetFriendly", amenity_tags=["pets_allowed"])
    other = _property(name="Other", amenity_tags=[])
    boosted = apply_amenity_boost([other, property_], ["pet friendly"])
    assert boosted[0].name == "PetFriendly"
