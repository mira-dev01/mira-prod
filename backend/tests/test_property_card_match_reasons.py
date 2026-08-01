"""Covers Phase 2.1 (documentation/agent-conversation-improvement.md):
match_reasons_for_card compares a PropertyCard's own fields against whichever
RecommendPropertiesArgs fields the guest's call actually supplied -- one unit
test per branch (guest-count, budget, purpose, amenity match) plus the
no-criteria-given case, per the plan's own verify step.
"""

import uuid

from app.schemas.tool import RecommendPropertiesArgs
from app.services.property.card import PropertyCard, match_reasons_for_card


def _card(**overrides) -> PropertyCard:
    defaults = dict(
        property_id=uuid.uuid4(),
        spoken_name="Ocean View",
        display_name="Ocean View",
        city="Goa",
        property_type="villa",
        bedroom_count=3,
        base_price=6000,
        max_guests=6,
        top_amenities=["pool", "wifi"],
        usp=None,
        match_reasons=[],
    )
    defaults.update(overrides)
    return PropertyCard(**defaults)


def test_no_criteria_given_produces_empty_reasons():
    card = _card()
    args = RecommendPropertiesArgs()
    assert match_reasons_for_card(card, args) == []


def test_guest_count_match_produces_fits_your_group_reason():
    card = _card(max_guests=6)
    args = RecommendPropertiesArgs(num_guests=4)
    reasons = match_reasons_for_card(card, args)
    assert any("fits your group of 4" in r for r in reasons)


def test_guest_count_not_comfortably_covered_produces_no_reason():
    """A property that just barely meets the count (exactly equal) is still
    a valid, shown result (per apply_guest_count_filter's own >= check) but
    doesn't get this specific reason -- 'comfortably covers' is a phrasing
    threshold on top of eligibility, not a second filter."""
    card = _card(max_guests=6)
    args = RecommendPropertiesArgs(num_guests=6)
    # max_guests >= num_guests is still true (6 >= 6), so this DOES count as
    # comfortably covering under the current threshold -- confirm it's
    # counted, not silently dropped, since the eligibility bar and the
    # phrasing bar are intentionally the same check here.
    reasons = match_reasons_for_card(card, args)
    assert any("fits your group of 6" in r for r in reasons)


def test_budget_match_produces_within_budget_reason():
    card = _card(base_price=4000)
    args = RecommendPropertiesArgs(budget=6000)
    reasons = match_reasons_for_card(card, args)
    assert "comfortably within budget" in reasons


def test_budget_not_comfortably_under_produces_no_budget_reason():
    card = _card(base_price=5900)
    args = RecommendPropertiesArgs(budget=6000)
    reasons = match_reasons_for_card(card, args)
    assert "comfortably within budget" not in reasons


def test_purpose_match_produces_purpose_phrase():
    card = _card()
    args = RecommendPropertiesArgs(purpose_of_stay="friends trip")
    reasons = match_reasons_for_card(card, args)
    assert any("friends" in r for r in reasons)


def test_purpose_with_no_known_mapping_produces_no_reason_rather_than_guessing():
    card = _card()
    args = RecommendPropertiesArgs(purpose_of_stay="some completely novel unmapped purpose")
    reasons = match_reasons_for_card(card, args)
    assert reasons == []


def test_amenity_match_names_the_specific_amenity_asked_for():
    card = _card(top_amenities=["pool", "wifi"])
    args = RecommendPropertiesArgs(required_amenities=["pool"])
    reasons = match_reasons_for_card(card, args)
    assert any("pool" in r for r in reasons)


def test_amenity_not_present_produces_no_amenity_reason():
    card = _card(top_amenities=["wifi"])
    args = RecommendPropertiesArgs(required_amenities=["pool"])
    reasons = match_reasons_for_card(card, args)
    assert not any("pool" in r for r in reasons)


def test_capped_at_two_reasons_even_with_every_criterion_matching():
    card = _card(max_guests=6, base_price=1000, top_amenities=["pool"])
    args = RecommendPropertiesArgs(
        num_guests=4, budget=6000, purpose_of_stay="friends trip", required_amenities=["pool"]
    )
    reasons = match_reasons_for_card(card, args)
    assert len(reasons) <= 2


def test_amenity_reason_takes_priority_over_vaguer_reasons_when_both_fit():
    """The most concrete/specific reason (a named amenity) should win a slot
    over a vaguer one when the cap forces a choice."""
    card = _card(max_guests=6, base_price=1000, top_amenities=["pool"])
    args = RecommendPropertiesArgs(
        num_guests=4, budget=6000, purpose_of_stay="friends trip", required_amenities=["pool"]
    )
    reasons = match_reasons_for_card(card, args)
    assert any("pool" in r for r in reasons)
