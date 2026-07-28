import asyncio

from app.models.property import Property
from app.models.property_chunk import PropertyChunk
from app.services import embedding_service
from app.services.property.retrieval import semantic_search


async def test_run_semantic_search_disabled_by_settings_returns_empty(test_user, db_session, monkeypatch):
    monkeypatch.setattr("app.config.settings.enable_semantic_property_search", False)
    result = await semantic_search.run_semantic_search(db_session, "romantic getaway", [])
    assert result == []


async def test_run_semantic_search_blank_query_returns_empty(test_user, db_session):
    result = await semantic_search.run_semantic_search(db_session, "   ", [])
    assert result == []


async def test_run_semantic_search_no_candidate_ids_returns_empty(test_user, db_session):
    result = await semantic_search.run_semantic_search(db_session, "romantic getaway", [])
    assert result == []


async def test_run_semantic_search_get_embedding_failure_falls_back_to_empty(test_user, db_session, monkeypatch):
    # No embedding provider configured in the test env -- get_embedding
    # already returns None cleanly (see embedding_service.py), so this
    # exercises the real fail-open path with no mocking needed.
    property_ = Property(user_id=test_user.id, name="Test", base_price=3000, max_guests=2, exophone="+918011119001")
    db_session.add(property_)
    await db_session.commit()
    await db_session.refresh(property_)

    result = await semantic_search.run_semantic_search(db_session, "romantic getaway", [property_.id])
    assert result == []


async def test_run_semantic_search_timeout_falls_back_to_empty(test_user, db_session, monkeypatch):
    # Regression/contract test: semantic search must NEVER let a slow
    # embedding call add unbounded latency to a live guest turn -- a hung
    # get_embedding call must be cut off at the configured timeout and
    # degrade to [] rather than propagate.
    property_ = Property(user_id=test_user.id, name="Test", base_price=3000, max_guests=2, exophone="+918011119002")
    db_session.add(property_)
    await db_session.commit()
    await db_session.refresh(property_)

    async def _hangs_forever(text):
        await asyncio.sleep(10)
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(embedding_service, "get_embedding", _hangs_forever)

    result = await semantic_search.run_semantic_search(
        db_session, "romantic getaway", [property_.id], timeout_seconds=0.05
    )
    assert result == []


async def test_run_semantic_search_matches_property_above_threshold(test_user, db_session, monkeypatch):
    property_ = Property(user_id=test_user.id, name="Cozy Cabin", base_price=3000, max_guests=2, exophone="+918011119003")
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

    result = await semantic_search.run_semantic_search(db_session, "romantic getaway", [property_.id])
    assert len(result) == 1
    assert result[0].id == property_.id


async def test_run_semantic_search_ignores_chunks_below_threshold(test_user, db_session, monkeypatch):
    property_ = Property(user_id=test_user.id, name="Unrelated", base_price=3000, max_guests=2, exophone="+918011119004")
    db_session.add(property_)
    await db_session.commit()
    await db_session.refresh(property_)

    chunk = PropertyChunk(
        property_id=property_.id, chunk_type="overview", text="A busy city apartment.",
        embedding=[0.0, 1.0, 0.0],
    )
    db_session.add(chunk)
    await db_session.commit()

    async def _fake_get_embedding(text):
        return [1.0, 0.0, 0.0]  # orthogonal -- cosine similarity 0.0

    monkeypatch.setattr(embedding_service, "get_embedding", _fake_get_embedding)

    result = await semantic_search.run_semantic_search(db_session, "romantic getaway", [property_.id])
    assert result == []


async def test_run_semantic_search_ignores_irrelevant_chunk_types(test_user, db_session, monkeypatch):
    property_ = Property(user_id=test_user.id, name="Test", base_price=3000, max_guests=2, exophone="+918011119005")
    db_session.add(property_)
    await db_session.commit()
    await db_session.refresh(property_)

    # "amenities" is not in the relevant chunk-type set for subjective
    # purpose-of-stay matching -- must be ignored even with a perfect score.
    chunk = PropertyChunk(
        property_id=property_.id, chunk_type="amenities", text="Pool, wifi, ac.",
        embedding=[1.0, 0.0, 0.0],
    )
    db_session.add(chunk)
    await db_session.commit()

    async def _fake_get_embedding(text):
        return [1.0, 0.0, 0.0]

    monkeypatch.setattr(embedding_service, "get_embedding", _fake_get_embedding)

    result = await semantic_search.run_semantic_search(db_session, "romantic getaway", [property_.id])
    assert result == []
