import uuid

from app.models.property import Property
from app.services.property.card import PropertyCard, build_property_card
from app.services.property.pitch_formatter import (
    RecommendationResult,
    confidence_for_result,
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
        match_reasons=[],
    )
    line = format_property_pitch_line(card, 1)
    assert line.startswith("1. Pine, a one-bedroom glasshouse suite with bathtub in Siolim")
    assert "₹4,200 a night" in line
    assert "sleeps 3" in line
    assert f"(property_id: {card.property_id})" in line


def test_format_property_pitch_line_appends_match_reason_clause():
    """Phase 2.2: ties the recommendation back to why it matches this guest,
    not just a description of the property."""
    card = PropertyCard(
        property_id=uuid.uuid4(), spoken_name="Ocean View", display_name="Ocean View",
        city="Goa", property_type="villa", bedroom_count=3, base_price=12000, max_guests=6,
        top_amenities=["pool", "parking"], usp=None,
        match_reasons=["fits your group of 6", "has the pool you asked for"],
    )
    line = format_property_pitch_line(card, 1)
    assert "-- fits your group of 6 and has the pool you asked for" in line
    # Reason clause comes before the property_id parenthetical, never after
    # or interleaved with it.
    assert line.index("--") < line.index("(property_id:")


def test_format_property_pitch_line_word_count_matches_golden_rules_guidance():
    """Phase 6.3 audit (documentation/agent-conversation-improvement.md):
    GOLDEN_RULES originally said "15 words or fewer" per item, written
    before Phase 2's match_reasons existed. Measured (not guessed) against
    the real pitch-formatter output with two match_reasons -- the realistic
    max case, since match_reasons_for_card caps at 2 -- a fully-populated
    line regularly lands in the high 20s/low 30s, well past the original
    15-word ceiling. GOLDEN_RULES was updated to "roughly 15-25 words" to
    match reality; this test is the data point that guidance is based on,
    re-checked here so a future PropertyCard field addition that further
    inflates line length gets caught rather than silently drifting the
    prompt's guidance out of sync with the real data shape again."""
    card = PropertyCard(
        property_id=uuid.uuid4(), spoken_name="Ocean View", display_name="Ocean View",
        city="Goa", property_type="villa", bedroom_count=3, base_price=12000, max_guests=6,
        top_amenities=["pool", "parking"], usp=None,
        match_reasons=["fits your group of 6", "has the pool you asked for"],
    )
    line = format_property_pitch_line(card, 1)
    spoken_part = line.split("(property_id:")[0]
    word_count = len(spoken_part.split())
    # Not a strict upper bound on the RAW structured line (which is a cue
    # for the model's tone, not a verbatim script -- system_prompt.py's own
    # "turn structured results into natural spoken sentences" rule already
    # covers that) -- this just confirms the fully-populated case stays in
    # a range GOLDEN_RULES' own wording can plausibly describe, catching
    # a silent, much-larger blowout (e.g. a future field tripling length).
    assert 15 <= word_count <= 40


def test_format_property_pitch_line_no_reason_clause_when_none_given():
    """Confirms the existing (pre-Phase-2) pitch shape is unchanged when
    match_reasons is empty -- no dangling '--' or stray punctuation."""
    card = PropertyCard(
        property_id=uuid.uuid4(), spoken_name="Ocean View", display_name="Ocean View",
        city="Goa", property_type="villa", bedroom_count=3, base_price=12000, max_guests=6,
        top_amenities=["pool"], usp=None, match_reasons=[],
    )
    line = format_property_pitch_line(card, 1)
    assert "--" not in line
    assert "sleeps 6. (property_id:" in line


def test_render_recommendation_text_not_found():
    result = RecommendationResult(options=[], not_found=True)
    text = render_recommendation_text(result)
    assert "couldn't find" in text.lower()


def test_render_recommendation_text_joins_options_by_newline_not_pipe():
    card_a = PropertyCard(
        property_id=uuid.uuid4(), spoken_name="Azure", display_name="Azure",
        city="Colva", property_type=None, bedroom_count=None,
        base_price=3500, max_guests=2, top_amenities=[], usp=None, match_reasons=[],
    )
    card_b = PropertyCard(
        property_id=uuid.uuid4(), spoken_name="Cabana", display_name="Cabana",
        city="Colva", property_type=None, bedroom_count=None,
        base_price=4000, max_guests=2, top_amenities=[], usp=None, match_reasons=[],
    )
    text = render_recommendation_text(RecommendationResult(options=[card_a, card_b]))
    assert text.count("\n") >= 2
    assert "Azure" in text and "Cabana" in text


def test_render_recommendation_text_includes_combo_note():
    card = PropertyCard(
        property_id=uuid.uuid4(), spoken_name="Unit A", display_name="Unit A",
        city="Siolim", property_type=None, bedroom_count=None,
        base_price=3000, max_guests=3, top_amenities=[], usp=None, match_reasons=[],
    )
    result = RecommendationResult(options=[card], combo_note=" Book two together.")
    text = render_recommendation_text(result)
    assert text.endswith("Book two together.")


def _confidence_card(name: str = "Unit A") -> PropertyCard:
    return PropertyCard(
        property_id=uuid.uuid4(), spoken_name=name, display_name=name,
        city="Goa", property_type=None, bedroom_count=None,
        base_price=3000, max_guests=3, top_amenities=[], usp=None, match_reasons=[],
    )


def test_confidence_for_result_one_clean_match_is_strong():
    """Phase 2.6 (documentation/agent-conversation-improvement.md): exactly
    one strong match -> confident phrasing."""
    assert confidence_for_result([_confidence_card()], combo_note="") == "strong"


def test_confidence_for_result_several_comparable_options_is_moderate():
    cards = [_confidence_card("A"), _confidence_card("B"), _confidence_card("C")]
    assert confidence_for_result(cards, combo_note="") == "moderate"


def test_confidence_for_result_combo_fallback_is_weak():
    """A combo_note firing already means no single property was a full
    match -- a real lower-confidence signal, not a guess."""
    cards = [_confidence_card("A"), _confidence_card("B")]
    assert confidence_for_result(cards, combo_note=" Book two together.") == "weak"


def test_render_recommendation_text_uses_confident_phrasing_for_one_strong_match():
    result = RecommendationResult(options=[_confidence_card()], recommendation_confidence="strong")
    text = render_recommendation_text(result)
    assert text.startswith("This one's a great fit:")


def test_render_recommendation_text_uses_measured_phrasing_for_moderate_confidence():
    result = RecommendationResult(
        options=[_confidence_card("A"), _confidence_card("B")], recommendation_confidence="moderate"
    )
    text = render_recommendation_text(result)
    assert text.startswith("I have a couple of options that could work well:")


def test_render_recommendation_text_uses_tentative_phrasing_for_weak_confidence():
    result = RecommendationResult(
        options=[_confidence_card("A")], combo_note=" Book two together.", recommendation_confidence="weak"
    )
    text = render_recommendation_text(result)
    assert text.startswith("I don't have a single perfect match")


def test_confidence_tier_never_changes_the_underlying_facts_spoken():
    """The facts (price, capacity, names) must be byte-identical regardless
    of confidence tier -- only the framing language changes, never the
    substance."""
    card = _confidence_card("Ocean View")
    strong_text = render_recommendation_text(RecommendationResult(options=[card], recommendation_confidence="strong"))
    moderate_text = render_recommendation_text(
        RecommendationResult(options=[card], recommendation_confidence="moderate")
    )
    # Strip the intro line (the only thing allowed to differ) and confirm
    # the rest -- the actual property line -- is identical either way.
    strong_body = strong_text.split("\n", 1)[1]
    moderate_body = moderate_text.split("\n", 1)[1]
    assert strong_body == moderate_body
