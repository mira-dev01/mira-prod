import uuid

from app.models.property import Property
from app.services.property.card import PropertyCard, build_property_card
from app.services.property.pitch_formatter import (
    RecommendationResult,
    format_property_pitch_line,
    render_recommendation_text,
)


def _property(**overrides) -> Property:
    defaults = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Pine - Glasshouse Suite w/bathtub | Pause Project",
        city="Siolim",
        base_price=4200,
        max_guests=3,
        amenities=["Bathtub", "Wifi"],
    )
    defaults.update(overrides)
    return Property(**defaults)


def test_build_property_card_prefers_spoken_name():
    property_ = _property(spoken_name="Pine", display_name="Pine - Suite w/bathtub", property_type="glasshouse")
    card = build_property_card(property_)
    assert card.spoken_name == "Pine"
    assert card.display_name == "Pine - Suite w/bathtub"
    assert card.property_type == "glasshouse"


def test_build_property_card_falls_back_to_raw_name_then_name():
    # No spoken_name/display_name set at all -- must fall back to raw_name,
    # then plain `name`, never crash or leave the field blank.
    property_ = _property(raw_name="Pine - Glasshouse Suite w/bathtub | Pause Project")
    card = build_property_card(property_)
    assert card.spoken_name == "Pine - Glasshouse Suite w/bathtub | Pause Project"

    property_no_raw = _property(raw_name=None)
    card_no_raw = build_property_card(property_no_raw)
    assert card_no_raw.spoken_name == "Pine - Glasshouse Suite w/bathtub | Pause Project"


def test_build_property_card_prioritizes_notable_amenities_over_generic():
    # Regression, confirmed live 2026-07-28: a real call spoke "Bath and
    # Hairdryer" as a property's top amenities while its own title
    # advertised "pool & projector" -- top_amenities must prefer
    # differentiating amenities over baseline ones every listing has.
    property_ = _property(amenities=["Bath", "Hairdryer", "Cleaning products", "Private pool", "Projector", "Wifi"])
    card = build_property_card(property_)
    assert card.top_amenities == ["Private pool", "Projector"]


def test_format_property_pitch_line_reads_naturally():
    card = PropertyCard(
        property_id=uuid.uuid4(),
        spoken_name="Pine",
        display_name="Pine - Suite w/bathtub",
        city="Siolim",
        property_type="glasshouse suite",
        bedroom_count=1,
        base_price=4200,
        max_guests=3,
        top_amenities=["bathtub"],
        usp=None,
    )
    line = format_property_pitch_line(card, 1)
    assert line.startswith("1. Pine, a one-bedroom glasshouse suite with bathtub in Siolim")
    assert "₹4,200 a night" in line
    assert "sleeps 3" in line
    assert f"(property_id: {card.property_id})" in line


def test_render_recommendation_text_not_found():
    result = RecommendationResult(options=[], not_found=True)
    text = render_recommendation_text(result)
    assert "couldn't find" in text.lower()


def test_render_recommendation_text_joins_options_by_newline_not_pipe():
    card_a = PropertyCard(
        property_id=uuid.uuid4(), spoken_name="Azure", display_name="Azure",
        city="Colva", property_type=None, bedroom_count=None,
        base_price=3500, max_guests=2, top_amenities=[], usp=None,
    )
    card_b = PropertyCard(
        property_id=uuid.uuid4(), spoken_name="Cabana", display_name="Cabana",
        city="Colva", property_type=None, bedroom_count=None,
        base_price=4000, max_guests=2, top_amenities=[], usp=None,
    )
    text = render_recommendation_text(RecommendationResult(options=[card_a, card_b]))
    assert text.count("\n") >= 2
    assert "Azure" in text and "Cabana" in text


def test_render_recommendation_text_includes_combo_note():
    card = PropertyCard(
        property_id=uuid.uuid4(), spoken_name="Unit A", display_name="Unit A",
        city="Siolim", property_type=None, bedroom_count=None,
        base_price=3000, max_guests=3, top_amenities=[], usp=None,
    )
    result = RecommendationResult(options=[card], combo_note=" Book two together.")
    text = render_recommendation_text(result)
    assert text.endswith("Book two together.")
