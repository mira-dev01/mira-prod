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
