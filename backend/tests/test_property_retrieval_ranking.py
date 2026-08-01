import uuid

from app.models.property import Property
from app.services.property.retrieval.ranking import diversify_leading_candidates, merge_and_rank


def _property(**overrides) -> Property:
    defaults = dict(id=uuid.uuid4(), user_id=uuid.uuid4(), name="Test", base_price=3000, max_guests=2)
    defaults.update(overrides)
    return Property(**defaults)


def test_merge_and_rank_no_semantic_results_returns_sql_order_unchanged():
    a = _property(name="A")
    b = _property(name="B")
    result = merge_and_rank([a, b], [])
    assert result == [a, b]


def test_merge_and_rank_appends_new_semantic_matches_after_sql_results():
    a = _property(name="A")
    b = _property(name="B")
    result = merge_and_rank([a], [b])
    assert [p.name for p in result] == ["A", "B"]


def test_merge_and_rank_never_reorders_sql_results_ahead_of_semantic():
    # A semantic match that duplicates an existing SQL result must not be
    # re-inserted or change SQL's own ordering -- SQL order is always
    # authoritative.
    a = _property(name="A")
    b = _property(name="B")
    result = merge_and_rank([a, b], [b, a])
    assert [p.name for p in result] == ["A", "B"]


def test_diversify_leading_candidates_distribution_across_many_calls():
    """Phase 2.5 (documentation/agent-conversation-improvement.md): N
    'calls' with identical criteria against a candidate set containing 2+
    equally-good matches -- confirm the lead recommendation isn't always
    the same property across all N (measure actual distribution, not one
    run)."""
    a = _property(name="A", base_price=5000)
    b = _property(name="B", base_price=5100)  # within the 10% comparable band
    c = _property(name="C", base_price=5200)  # also within band

    leaders = set()
    for i in range(30):
        call_session_id = str(uuid.uuid4())
        result = diversify_leading_candidates([a, b, c], call_session_id)
        leaders.add(result[0].name)

    # With 30 distinct random call_session_ids, expect real variety, not
    # always the same property leading.
    assert len(leaders) > 1


def test_diversify_leading_candidates_same_call_id_is_stable():
    """A single call re-querying with the same criteria mid-call gets a
    stable, consistent answer -- no flip-flopping within one conversation."""
    a = _property(name="A", base_price=5000)
    b = _property(name="B", base_price=5100)
    call_session_id = str(uuid.uuid4())

    first = diversify_leading_candidates([a, b], call_session_id)
    second = diversify_leading_candidates([a, b], call_session_id)
    assert [p.name for p in first] == [p.name for p in second]


def test_diversify_leading_candidates_never_reorders_a_clearly_better_match():
    """A candidate set where one candidate is a clearly better fit (price
    well outside the comparable band) always recommends the genuinely
    better match first -- diversity only applies among truly comparable
    options, never at the cost of quality."""
    much_cheaper = _property(name="Cheap", base_price=2000)
    much_pricier = _property(name="Pricey", base_price=8000)

    for _ in range(10):
        result = diversify_leading_candidates([much_cheaper, much_pricier], str(uuid.uuid4()))
        assert result[0].name == "Cheap"


def test_diversify_leading_candidates_single_candidate_is_a_no_op():
    a = _property(name="A")
    assert diversify_leading_candidates([a], "some-call-id") == [a]


def test_diversify_leading_candidates_no_call_session_id_returns_unchanged_order():
    """Falls back to today's byte-identical ordering (no rotation) when no
    call_session_id is available, rather than guessing a seed."""
    a = _property(name="A", base_price=5000)
    b = _property(name="B", base_price=5100)
    result = diversify_leading_candidates([a, b], None)
    assert [p.name for p in result] == ["A", "B"]


def test_diversify_leading_candidates_never_touches_items_outside_the_band():
    """Only the comparable-band prefix is ever rotated -- anything clearly
    outside that band keeps its exact relative position."""
    a = _property(name="A", base_price=5000)
    b = _property(name="B", base_price=5050)  # in band with A
    c = _property(name="C", base_price=9000)  # far outside the band

    for _ in range(10):
        result = diversify_leading_candidates([a, b, c], str(uuid.uuid4()))
        assert result[-1].name == "C"
        assert {result[0].name, result[1].name} == {"A", "B"}


def test_diversify_leading_candidates_zero_price_fails_open():
    """A cheapest_price of 0 (exact_airbnb_pricing=True properties can
    legitimately have base_price=0) must not divide by zero -- fail open to
    the existing order."""
    zero = _property(name="Zero", base_price=0)
    other = _property(name="Other", base_price=100)
    result = diversify_leading_candidates([zero, other], "some-call-id")
    assert [p.name for p in result] == ["Zero", "Other"]
