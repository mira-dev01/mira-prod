from app.models.property import Property
from app.schemas.tool import RecommendPropertiesArgs
from app.services.property.retrieval.filter_builder import apply_landmark_boost, matches_landmark


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
