from datetime import date, timedelta

from app.models.booking import Booking
from app.models.property import Property
from app.models.property_chunk import PropertyChunk
from app.schemas.tool import RecommendPropertiesArgs
from app.services import embedding_service
from app.services.property.retrieval import orchestrator


async def test_orchestrator_returns_sql_only_when_purpose_of_stay_absent(test_user, db_session):
    property_ = Property(user_id=test_user.id, name="Pine", base_price=3000, max_guests=2, exophone="+918011129001")
    db_session.add(property_)
    await db_session.commit()

    args = RecommendPropertiesArgs()
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert len(result.options) == 1
    assert result.options[0].spoken_name == "Pine"


async def test_orchestrator_never_returns_more_than_three_options(test_user, db_session):
    properties = [
        Property(user_id=test_user.id, name=f"Unit{i}", base_price=1000 * i, max_guests=2, exophone=f"+9180{i:08d}")
        for i in range(1, 6)
    ]
    db_session.add_all(properties)
    await db_session.commit()

    args = RecommendPropertiesArgs()
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert len(result.options) <= 3


async def test_orchestrator_semantic_search_never_fires_for_pure_structured_query(test_user, db_session, monkeypatch):
    # No purpose_of_stay -- semantic search must not be invoked at all, even
    # if get_embedding would otherwise succeed. Patch get_embedding to raise
    # so this test fails loudly if semantic search is ever wrongly invoked.
    property_ = Property(user_id=test_user.id, name="Pine", base_price=3000, max_guests=2, exophone="+918011129002")
    db_session.add(property_)
    await db_session.commit()

    async def _should_not_be_called(text):
        raise AssertionError("get_embedding should never be called for a purely structured query")

    monkeypatch.setattr(embedding_service, "get_embedding", _should_not_be_called)

    args = RecommendPropertiesArgs(required_amenities=None, budget=5000)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert len(result.options) == 1


async def test_orchestrator_semantic_search_enriches_when_sql_underreturns(test_user, db_session, monkeypatch):
    # SQL returns zero results (budget too low for anything), but a
    # subjective purpose_of_stay is present -- semantic search should widen
    # to the host's full portfolio and find a match via chunk embeddings.
    property_ = Property(user_id=test_user.id, name="Cozy Cabin", base_price=9000, max_guests=2, exophone="+918011129003")
    db_session.add(property_)
    await db_session.commit()
    await db_session.refresh(property_)

    chunk = PropertyChunk(
        property_id=property_.id, chunk_type="overview", text="A romantic cozy cabin getaway.",
        embedding=[1.0, 0.0, 0.0],
    )
    db_session.add(chunk)
    await db_session.commit()

    async def _fake_get_embedding(text):
        return [1.0, 0.0, 0.0]

    monkeypatch.setattr(embedding_service, "get_embedding", _fake_get_embedding)

    args = RecommendPropertiesArgs(purpose_of_stay="romantic getaway")
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert any(card.spoken_name == "Cozy Cabin" for card in result.options)


async def test_handle_recommend_properties_delegates_to_orchestrator(test_user, db_session):
    from app.services import tool_handlers

    property_ = Property(user_id=test_user.id, name="Pine", base_price=3000, max_guests=2, exophone="+918011129004")
    db_session.add(property_)
    await db_session.commit()

    args = RecommendPropertiesArgs()
    result = await tool_handlers.handle_recommend_properties(db_session, args, test_user.id)
    assert result.options[0].spoken_name == "Pine"


async def test_recommend_properties_excludes_booked_property_when_dates_known(test_user, db_session):
    """Phase 2.4 (documentation/agent-conversation-improvement.md): two
    properties matching every other criterion, one with a conflicting
    Booking row for the guest's stated dates -- confirm only the available
    one is returned when dates are passed through."""
    booked = Property(
        user_id=test_user.id, name="Booked Villa", base_price=4000, max_guests=4, exophone="+918011129005"
    )
    available = Property(
        user_id=test_user.id, name="Open Villa", base_price=4200, max_guests=4, exophone="+918011129006"
    )
    db_session.add_all([booked, available])
    await db_session.commit()
    await db_session.refresh(booked)

    check_in = date.today() + timedelta(days=10)
    check_out = check_in + timedelta(days=2)
    db_session.add(
        Booking(property_id=booked.id, check_in=check_in, check_out=check_out, status="confirmed")
    )
    await db_session.commit()

    args = RecommendPropertiesArgs()
    result = await orchestrator.recommend_properties(
        db_session, args, test_user.id, check_in=check_in, check_out=check_out
    )

    names = [card.spoken_name for card in result.options]
    assert "Open Villa" in names
    assert "Booked Villa" not in names


async def test_recommend_properties_byte_identical_when_dates_not_known(test_user, db_session):
    """Confirms this is purely additive -- behavior when dates aren't yet
    known (the common case, per Phase 2.3's earlier-recommend goal) must be
    unchanged from before this task."""
    booked = Property(
        user_id=test_user.id, name="Booked Villa", base_price=4000, max_guests=4, exophone="+918011129007"
    )
    db_session.add(booked)
    await db_session.commit()
    await db_session.refresh(booked)

    check_in = date.today() + timedelta(days=10)
    check_out = check_in + timedelta(days=2)
    db_session.add(
        Booking(property_id=booked.id, check_in=check_in, check_out=check_out, status="confirmed")
    )
    await db_session.commit()

    args = RecommendPropertiesArgs()
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)

    # No dates passed -- today's unfiltered behavior, the booked property
    # still shows up (check_calendar catches the real conflict downstream).
    assert any(card.spoken_name == "Booked Villa" for card in result.options)


async def test_recommend_properties_confidence_strong_for_one_clean_match(test_user, db_session):
    property_ = Property(user_id=test_user.id, name="Pine", base_price=3000, max_guests=2, exophone="+918011129301")
    db_session.add(property_)
    await db_session.commit()

    args = RecommendPropertiesArgs()
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert result.recommendation_confidence == "strong"


async def test_recommend_properties_confidence_weak_for_combo_fallback(test_user, db_session):
    unit_a = Property(user_id=test_user.id, name="Unit A", base_price=3000, max_guests=3, exophone="+918011129302")
    unit_b = Property(user_id=test_user.id, name="Unit B", base_price=3200, max_guests=3, exophone="+918011129303")
    db_session.add_all([unit_a, unit_b])
    await db_session.commit()

    args = RecommendPropertiesArgs(num_guests=6)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert result.recommendation_confidence == "weak"


async def test_recommend_properties_diversifies_leader_across_calls_with_comparable_options(test_user, db_session):
    """Phase 2.5 (documentation/agent-conversation-improvement.md), wired
    end-to-end through orchestrator.recommend_properties: two comparably
    priced properties, many distinct call_session_ids -- confirm real
    variety in which one leads, not always the cheapest."""
    import uuid as uuid_module

    a = Property(user_id=test_user.id, name="A", base_price=5000, max_guests=2, exophone="+918011129201")
    b = Property(user_id=test_user.id, name="B", base_price=5100, max_guests=2, exophone="+918011129202")
    db_session.add_all([a, b])
    await db_session.commit()

    leaders = set()
    for _ in range(20):
        args = RecommendPropertiesArgs()
        result = await orchestrator.recommend_properties(
            db_session, args, test_user.id, call_session_id=uuid_module.uuid4()
        )
        leaders.add(result.options[0].spoken_name)

    assert len(leaders) > 1


async def test_recommend_properties_no_call_session_id_uses_stable_default_order(test_user, db_session):
    """Confirms this is purely additive when call_session_id isn't passed --
    behavior byte-identical to before this task."""
    a = Property(user_id=test_user.id, name="A", base_price=5000, max_guests=2, exophone="+918011129203")
    b = Property(user_id=test_user.id, name="B", base_price=5100, max_guests=2, exophone="+918011129204")
    db_session.add_all([a, b])
    await db_session.commit()

    args = RecommendPropertiesArgs()
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert result.options[0].spoken_name == "A"


async def test_recommend_properties_availability_check_fails_open_on_error(test_user, db_session, monkeypatch):
    """Must fail open -- an availability pre-check erroring/timing out is
    never a reason a recommendation should be blocked entirely."""
    property_ = Property(
        user_id=test_user.id, name="Pine", base_price=3000, max_guests=2, exophone="+918011129008"
    )
    db_session.add(property_)
    await db_session.commit()

    from app.services import calendar_service

    async def _boom(db, property_ids, check_in, check_out):
        raise RuntimeError("simulated DB error")

    monkeypatch.setattr(calendar_service, "unavailable_property_ids", _boom)

    args = RecommendPropertiesArgs()
    check_in = date.today() + timedelta(days=5)
    check_out = check_in + timedelta(days=2)
    result = await orchestrator.recommend_properties(
        db_session, args, test_user.id, check_in=check_in, check_out=check_out
    )

    assert result.options[0].spoken_name == "Pine"
