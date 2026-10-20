"""Covers comparison_notes (Recommendation engine v2 -- "why not that one" /
tradeoff reasoning): for each PropertyCard in a recommend_properties result
set, one clause naming its clearest difference from the CHEAPEST other card
in the same set -- one unit test per branch (price difference, capacity
difference, no meaningful difference, single/empty input, non-positive
cheapest price) per this codebase's own established convention
(test_property_card_match_reasons.py).
"""

import uuid

from app.services.property.card import PropertyCard, comparison_notes


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
        top_amenities=[],
        usp=None,
        match_reasons=[],
        comparison_note="",
        is_premium=False,
        amenity_checklist="",
    )
    defaults.update(overrides)
    return PropertyCard(**defaults)


def test_empty_list_produces_no_notes():
    assert comparison_notes([]) == {}


def test_single_card_produces_no_notes():
    """Nothing to compare against -- same reasoning as
    confidence_for_result's own "strong" (exactly one match) case having
    nothing to differ from."""
    card = _card()
    assert comparison_notes([card]) == {}


def test_cheapest_card_gets_no_note():
    cheapest = _card(spoken_name="Palm Retreat", base_price=5000)
    pricier = _card(spoken_name="Ocean View", base_price=6000)
    notes = comparison_notes([cheapest, pricier])
    assert cheapest.property_id not in notes


def test_meaningful_price_gap_produces_a_price_note_naming_the_cheaper_card():
    cheapest = _card(spoken_name="Palm Retreat", base_price=5000, max_guests=4)
    # 20% more than cheapest -- above the 15% meaningful-gap threshold.
    pricier = _card(spoken_name="Ocean View", base_price=6000, max_guests=4)
    notes = comparison_notes([cheapest, pricier])
    assert "₹1,000 more than Palm Retreat" in notes[pricier.property_id]


def test_small_price_gap_falls_through_to_capacity_check_instead():
    """A price difference under the meaningful-gap threshold isn't worth
    voicing as a reason -- confirms the function checks capacity next
    rather than reporting a trivial price gap."""
    cheapest = _card(spoken_name="Palm Retreat", base_price=5000, max_guests=4)
    # ~2% more than cheapest -- well under the 15% threshold.
    slightly_pricier = _card(spoken_name="Sea Breeze", base_price=5100, max_guests=8)
    notes = comparison_notes([cheapest, slightly_pricier])
    assert "sleeps 4 more than Palm Retreat" in notes[slightly_pricier.property_id]


def test_meaningful_capacity_gap_alone_produces_a_capacity_note():
    cheapest = _card(spoken_name="Palm Retreat", base_price=5000, max_guests=2)
    bigger = _card(spoken_name="Sea Breeze", base_price=5000, max_guests=5)
    notes = comparison_notes([cheapest, bigger])
    assert "sleeps 3 more than Palm Retreat" in notes[bigger.property_id]


def test_no_meaningful_difference_produces_no_note_at_all():
    """Neither the price nor the capacity gap crosses its own threshold --
    must not fabricate a note out of a trivial difference."""
    cheapest = _card(spoken_name="Palm Retreat", base_price=5000, max_guests=4)
    similar = _card(spoken_name="Sea Breeze", base_price=5050, max_guests=5)
    notes = comparison_notes([cheapest, similar])
    assert similar.property_id not in notes


def test_only_one_clause_per_card_price_checked_before_capacity():
    """Price is checked first -- a card that differs meaningfully on BOTH
    price and capacity gets only the price note, never both stacked into
    one clause (same one-clause discipline match_reasons_for_card already
    enforces via its own 2-reason cap)."""
    cheapest = _card(spoken_name="Palm Retreat", base_price=5000, max_guests=2)
    pricier_and_bigger = _card(spoken_name="Ocean View", base_price=7000, max_guests=6)
    notes = comparison_notes([cheapest, pricier_and_bigger])
    note = notes[pricier_and_bigger.property_id]
    assert "more than Palm Retreat a night" in note
    assert "sleeps" not in note


def test_non_positive_cheapest_price_produces_no_notes_at_all():
    """Comparing against a property with no real rate on file would produce
    a nonsensical percentage -- fails open to no notes rather than a
    divide-by-zero or a misleading comparison. filter_builder.py already
    excludes zero-price properties upstream in the real call path; this
    stays defensive rather than assuming that guarantee holds for every
    future caller of this function directly."""
    zero_priced = _card(spoken_name="Free House", base_price=0)
    other = _card(spoken_name="Ocean View", base_price=6000)
    assert comparison_notes([zero_priced, other]) == {}


def test_three_cards_each_compared_against_the_single_cheapest():
    """Deterministic single baseline (the cheapest), not an every-pair
    matrix -- confirms a third card is also compared against the same
    cheapest card, not against its own nearest neighbor."""
    cheapest = _card(spoken_name="Palm Retreat", base_price=5000, max_guests=4)
    mid = _card(spoken_name="Sea Breeze", base_price=6500, max_guests=4)
    priciest = _card(spoken_name="Ocean View", base_price=8000, max_guests=4)
    notes = comparison_notes([cheapest, mid, priciest])
    assert cheapest.property_id not in notes
    assert "Palm Retreat" in notes[mid.property_id]
    assert "Palm Retreat" in notes[priciest.property_id]


def test_pricier_but_smaller_option_gets_a_fewer_guests_note():
    """Regression: a pricier option that ALSO sleeps fewer people than the
    cheapest (a common real shape -- a large cheap family villa vs. a small
    pricier boutique unit) previously produced NO note at all, since the
    capacity check only ever looked for a POSITIVE gap ("more" than the
    cheapest). The direction must flip correctly when the pricier option is
    the smaller one -- this is arguably the single clearest tradeoff a
    guest would want surfaced, not a case to silently drop."""
    cheap_big = _card(spoken_name="Family Villa", base_price=5000, max_guests=8)
    # Only ~2% pricier -- below the price-gap threshold, falls through to
    # the capacity check, where it sleeps 6 FEWER than the cheapest.
    pricier_small = _card(spoken_name="Boutique Suite", base_price=5100, max_guests=2)
    notes = comparison_notes([cheap_big, pricier_small])
    assert "sleeps 6 fewer than Family Villa" in notes[pricier_small.property_id]


def test_unreliable_price_ids_excludes_a_card_from_ever_being_the_price_baseline():
    """exact_airbnb_pricing properties' stored base_price can be stale or a
    placeholder (their real price comes from a live fetch at get_pricing
    time) -- a flagged card must never be picked as the cheapest baseline
    for a PRICE comparison, even if its stored price happens to be the
    lowest number in the set. Same discipline handle_get_pricing/
    handle_negotiate_rate's own base_price=0 guard already applies; this
    function must not reopen that failure shape via a new spoken claim."""
    unreliable_cheap = _card(spoken_name="Stale Listing", base_price=100, max_guests=4)
    real = _card(spoken_name="Ocean View", base_price=6000, max_guests=4)
    notes = comparison_notes([unreliable_cheap, real], unreliable_price_ids=frozenset([unreliable_cheap.property_id]))
    # real.base_price (6000) is now the only trustworthy price -- it becomes
    # the baseline, so it gets no note (nothing to compare IT against).
    assert real.property_id not in notes


def test_unreliable_price_ids_flagged_card_never_produces_a_price_note_about_itself():
    """A flagged card must never appear as the SUBJECT of a price
    comparison either (e.g. "₹5,900 more than X a night" built from its own
    stale number) -- only a capacity comparison is safe to make about it."""
    unreliable = _card(spoken_name="Stale Listing", base_price=100, max_guests=4)
    real = _card(spoken_name="Ocean View", base_price=6000, max_guests=4)
    notes = comparison_notes([unreliable, real], unreliable_price_ids=frozenset([unreliable.property_id]))
    # Same max_guests as the real baseline -- no meaningful capacity
    # difference either, so the flagged card correctly gets NO note at all
    # (never a fabricated price claim built from its own unreliable number).
    assert unreliable.property_id not in notes


def test_unreliable_price_ids_flagged_card_can_still_get_a_capacity_note():
    """Capacity is a real, trustworthy fact regardless of whether the
    price is -- a flagged card should still get a capacity comparison
    against the real cheapest if the guest-count gap is meaningful."""
    unreliable = _card(spoken_name="Stale Listing", base_price=100, max_guests=2)
    real = _card(spoken_name="Ocean View", base_price=6000, max_guests=6)
    notes = comparison_notes([unreliable, real], unreliable_price_ids=frozenset([unreliable.property_id]))
    assert "sleeps 4 fewer than Ocean View" in notes[unreliable.property_id]


def test_all_cards_unreliable_priced_produces_no_notes_at_all():
    """If every card's price is unreliable, there's no valid card left to
    serve as ANY baseline (price or capacity) -- fails open to no notes at
    all rather than falling back to comparing untrustworthy stored prices
    against each other, or picking an arbitrary capacity baseline from
    cards whose price can't be trusted."""
    a = _card(spoken_name="Stale A", base_price=100, max_guests=2)
    b = _card(spoken_name="Stale B", base_price=200, max_guests=8)
    notes = comparison_notes([a, b], unreliable_price_ids=frozenset([a.property_id, b.property_id]))
    assert notes == {}
