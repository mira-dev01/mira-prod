import uuid

from app.models.property import Property
from app.services.property.chunking import build_property_chunks


def _property(**overrides) -> Property:
    defaults = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Pine - Glasshouse Suite w/bathtub | Pause Project",
        display_name="Pine - Suite w/bathtub",
        property_type="glasshouse",
        city="Siolim",
        base_price=4200,
        max_guests=3,
    )
    defaults.update(overrides)
    return Property(**defaults)


def test_overview_chunk_uses_display_name_and_usp():
    property_ = _property(usp="A cozy forest getaway.")
    chunks = build_property_chunks(property_)
    assert "overview" in chunks
    assert "Pine - Suite w/bathtub" in chunks["overview"]
    assert "cozy forest getaway" in chunks["overview"]


def test_amenities_chunk_only_present_when_amenities_exist():
    without = build_property_chunks(_property(amenities=[]))
    assert "amenities" not in without

    with_amenities = build_property_chunks(_property(amenities=["Pool", "Wifi"]))
    assert "Pool" in with_amenities["amenities"]
    assert "Wifi" in with_amenities["amenities"]


def test_location_chunk_combines_neighborhood_info_and_landmarks():
    property_ = _property(
        neighborhood_info="Quiet residential lane.",
        landmarks=[{"name": "Thalassa", "distance_minutes": 5, "mode": "walk"}],
    )
    chunks = build_property_chunks(property_)
    assert "Quiet residential lane." in chunks["location"]
    assert "Thalassa" in chunks["location"]
    assert "5 minutes" in chunks["location"]


def test_house_rules_chunk_only_present_when_set():
    without = build_property_chunks(_property(house_rules=None))
    assert "house_rules" not in without

    with_rules = build_property_chunks(_property(house_rules="No smoking indoors."))
    assert with_rules["house_rules"] == "No smoking indoors."


def test_reviews_chunk_never_generated():
    # No review text exists in the import schema yet -- "reviews" is
    # reserved but must never appear from build_property_chunks today.
    chunks = build_property_chunks(_property(amenities=["Pool"], house_rules="Quiet hours 10pm."))
    assert "reviews" not in chunks


def test_property_with_no_content_produces_only_overview():
    # A bare-minimum property still gets an overview chunk (it always has
    # at least a name), but nothing else.
    property_ = _property(display_name=None, usp=None, amenities=[], house_rules=None, neighborhood_info=None)
    chunks = build_property_chunks(property_)
    assert set(chunks.keys()) == {"overview"}
