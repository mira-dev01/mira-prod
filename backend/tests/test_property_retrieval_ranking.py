import uuid

from app.models.property import Property
from app.services.property.retrieval.ranking import merge_and_rank


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
