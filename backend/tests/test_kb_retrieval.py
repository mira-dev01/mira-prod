"""search_faq's knowledge-base retrieval (app/services/property/kb_retrieval.py)
and the handle_search_faq contract built on it."""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

from app.models.faq_entry import FaqEntry
from app.models.notification import Notification
from app.models.property import Property
from app.models.unanswered_question import UnansweredQuestion
from app.schemas.tool import SearchFaqArgs
from app.services import embedding_service, tool_handlers
from app.services.property import kb_retrieval
from app.services.property.chunking import build_fact_chunks, sync_property_facts
from app.services.property.kb_retrieval import Fact, search_facts


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    kb_retrieval.invalidate_fact_cache()

    async def _none(text):
        return None

    monkeypatch.setattr(embedding_service, "get_embedding", _none)
    yield
    kb_retrieval.invalidate_fact_cache()


def _facts(**overrides) -> list[Fact]:
    fields = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Sunset Villa",
        city="Manali",
        base_price=5000,
        check_in_time="14:00",
        check_out_time="11:00",
        max_guests=6,
        minimum_nights=1,
        faq=[],
        amenities=["WiFi", "Free parking", "Kitchen", "Swimming pool"],
        landmarks=[],
        seasonal_notes=[],
        house_rules="No smoking indoors.\nPets are not allowed.\nQuiet hours after 10 PM.",
        neighborhood_info="Mall Road is a 10 minute drive away. The nearest bus stand is 2 km from the villa.",
        usp="A cosy wooden cottage with mountain views.",
    )
    fields.update(overrides)
    prop = Property(**fields)
    return [Fact(None, d.text, d.source_kind, d.source_ref, d.meta, None) for d in build_fact_chunks(prop, [])]


async def _search(query, facts=None, portfolio=None, extra="Sunset Villa Manali"):
    return await search_facts(query, facts if facts is not None else _facts(), portfolio or [], extra)


async def test_direct_match_is_found():
    result = await _search("pets allowed")
    assert result.status == "found"
    assert any("Pets are not allowed" in f.text for f in result.facts)


async def test_synonym_matches_amenity():
    result = await _search("is there internet")
    assert result.status == "found"
    assert any("WiFi" in f.text for f in result.facts)


async def test_check_in_time_question():
    result = await _search("what time is check in")
    assert result.status == "found"
    assert any("Check-in time is 14:00" in f.text for f in result.facts)


async def test_distance_to_place_on_file():
    result = await _search("how far is Mall Road")
    assert result.status == "found"
    assert any("10 minute drive" in f.text for f in result.facts)


async def test_the_delhi_incident_nothing_on_file_is_a_miss():
    """Regression: 'how far from Delhi' used to be answered from world
    knowledge ('6 hours') because the whole property blob was returned."""
    result = await _search("how far is Delhi")
    assert result.status == "none"
    assert result.facts == []
    assert result.not_on_file_terms == ["Delhi"]


async def test_place_named_but_not_on_file_downgrades_a_loose_match():
    # 'Manali' (property city) and 'distance' match something, but Delhi is on file nowhere.
    result = await _search("distance from Delhi to the villa by car")
    assert result.status in ("none", "possible")
    assert "Delhi" in result.not_on_file_terms


async def test_unrelated_question_is_a_miss():
    result = await _search("do you have a swimming instructor for kids")
    assert result.status in ("none", "possible")
    assert not (result.status == "found")


async def test_general_question_returns_overview():
    result = await _search("tell me about this place")
    assert result.status == "found"
    assert any(f.source_kind in ("description", "structured") for f in result.facts)


async def test_no_facts_at_all_is_a_miss():
    result = await search_facts("pets allowed", [], [], "")
    assert result.status == "none"


async def test_out_of_season_note_is_not_served(monkeypatch):
    facts = _facts(seasonal_notes=[{"note": "Pool closed for maintenance.", "start_month": 1, "end_month": 1}])
    month = datetime.now(kb_retrieval.IST).month
    facts = _facts(seasonal_notes=[{"note": "Heated floors installed zzzmarker.", "start_month": (month % 12) + 1, "end_month": (month % 12) + 1}])
    result = await _search("heated floors zzzmarker", facts)
    assert not any("zzzmarker" in f.text for f in result.facts)


async def test_in_season_note_is_served():
    month = datetime.now(kb_retrieval.IST).month
    facts = _facts(seasonal_notes=[{"note": "Bonfire available zzzmarker.", "start_month": month, "end_month": month}])
    result = await _search("bonfire zzzmarker", facts)
    assert any("zzzmarker" in f.text for f in result.facts)


async def test_portfolio_wide_faq_is_searchable():
    portfolio = [Fact(uuid.uuid4(), "Q: What is your cancellation policy? A: Free until 7 days before.", "faq_entry")]
    result = await search_facts("cancellation policy", [], portfolio, "")
    assert result.status == "found"


async def test_embedding_path_finds_paraphrase_lexical_misses(monkeypatch):
    async def fake_embedding(text):
        return [1.0, 0.0] if "furry" in text else [0.0, 1.0]

    monkeypatch.setattr(embedding_service, "get_embedding", fake_embedding)
    facts = [
        Fact(None, "House rule: Dogs are welcome with prior notice.", "house_rules", embedding=[1.0, 0.0]),
        Fact(None, "Amenities: WiFi.", "amenity", embedding=[0.0, 1.0]),
    ]
    result = await search_facts("can I bring my furry companion", facts, [], "")
    assert result.status == "found"
    assert result.used_embedding
    assert "Dogs are welcome" in result.facts[0].text


async def test_embedding_failure_degrades_to_lexical_not_to_guessing():
    facts = [Fact(None, "House rule: Dogs are welcome.", "house_rules", embedding=[1.0, 0.0])]
    result = await search_facts("can I bring my furry companion", facts, [], "")
    assert result.status == "none"
    assert not result.used_embedding


# --- handle_search_faq integration ------------------------------------------


async def test_search_faq_returns_only_matching_facts_not_the_whole_blob(db_session, test_property):
    test_property.neighborhood_info = "Mall Road is a 10 minute drive away."
    test_property.house_rules = "No smoking indoors."
    await db_session.commit()
    args = SearchFaqArgs(query="Is smoking allowed?", property_id=str(test_property.id))

    text = await tool_handlers.handle_search_faq(db_session, args, test_property.user_id, test_property.id)

    assert "No smoking indoors" in text
    assert "Mall Road" not in text
    assert text.startswith("KNOWLEDGE BASE FACTS")


async def test_search_faq_miss_logs_gap_with_property_and_source(db_session, test_property, test_call_session):
    args = SearchFaqArgs(query="How far is Delhi?", property_id=str(test_property.id))

    text = await tool_handlers.handle_search_faq(
        db_session, args, test_property.user_id, test_property.id, test_call_session.id
    )

    assert "don't have verified information" in text
    assert 'NOT ON FILE: "Delhi"' in text
    gaps = (await db_session.scalars(select(UnansweredQuestion))).all()
    assert len(gaps) == 1
    assert gaps[0].property_id == test_property.id
    assert gaps[0].call_session_id == test_call_session.id
    assert gaps[0].source == "search_miss"
    assert gaps[0].status == "pending"


async def test_search_faq_miss_is_deduped_within_a_call(db_session, test_property, test_call_session):
    args = SearchFaqArgs(query="How far is Delhi?", property_id=str(test_property.id))
    for _ in range(3):
        await tool_handlers.handle_search_faq(
            db_session, args, test_property.user_id, test_property.id, test_call_session.id
        )
    assert len((await db_session.scalars(select(UnansweredQuestion))).all()) == 1


async def test_one_host_notification_per_call_aggregates_questions(db_session, test_property, test_call_session):
    for query in ("How far is Delhi?", "Is there a gym nearby?"):
        await tool_handlers.handle_search_faq(
            db_session,
            SearchFaqArgs(query=query, property_id=str(test_property.id)),
            test_property.user_id,
            test_property.id,
            test_call_session.id,
        )
    notes = (
        await db_session.scalars(select(Notification).where(Notification.call_session_id == test_call_session.id))
    ).all()
    assert len(notes) == 1
    assert "How far is Delhi?" in notes[0].message and "Is there a gym nearby?" in notes[0].message
    assert notes[0].property_id == test_property.id
    assert notes[0].urgency == "low"


async def test_search_faq_hit_logs_no_gap(db_session, test_property):
    args = SearchFaqArgs(query="Is wifi free?", property_id=str(test_property.id))
    text = await tool_handlers.handle_search_faq(db_session, args, test_property.user_id, test_property.id)
    assert "Yes." in text
    assert (await db_session.scalars(select(UnansweredQuestion))).all() == []


async def test_search_faq_on_result_receives_structured_result(db_session, test_property):
    seen = []
    await tool_handlers.handle_search_faq(
        db_session,
        SearchFaqArgs(query="How far is Delhi?", property_id=str(test_property.id)),
        test_property.user_id,
        test_property.id,
        on_result=lambda prop, result: seen.append((prop, result)),
    )
    assert seen[0][0].id == test_property.id
    assert seen[0][1].status == "none"


async def test_search_faq_without_a_property_asks_which_one(db_session, test_property):
    text = await tool_handlers.handle_search_faq(
        db_session, SearchFaqArgs(query="Do you allow pets?", property_id=None), test_property.user_id, None
    )
    assert "ask which one" in text


async def test_property_without_persisted_facts_is_answered_from_live_data(db_session, test_property):
    """Deploying before the backfill must not regress: no fact rows yet ->
    facts are built in memory from the live property."""
    test_property.house_rules = "Check-in is self-service with a keypad."
    await db_session.commit()
    kb_retrieval.invalidate_fact_cache()

    text = await tool_handlers.handle_search_faq(
        db_session,
        SearchFaqArgs(query="keypad self-service check in", property_id=str(test_property.id)),
        test_property.user_id,
        test_property.id,
    )
    assert "keypad" in text


async def test_edited_property_is_not_answered_from_stale_facts(db_session, test_property):
    test_property.house_rules = "Old rule: no pets."
    await db_session.commit()
    await sync_property_facts(db_session, test_property)
    kb_retrieval.invalidate_fact_cache()

    test_property.house_rules = "New rule: pets welcome."
    await db_session.commit()  # bumps updated_at past the synced facts; no resync

    text = await tool_handlers.handle_search_faq(
        db_session,
        SearchFaqArgs(query="pets", property_id=str(test_property.id)),
        test_property.user_id,
        test_property.id,
    )
    assert "pets welcome" in text
    assert "no pets" not in text


async def test_host_answer_to_a_gap_is_found_on_the_next_search(db_session, test_property, test_call_session):
    from app.services import faq_service

    args = SearchFaqArgs(query="Is there a gym nearby?", property_id=str(test_property.id))
    first = await tool_handlers.handle_search_faq(
        db_session, args, test_property.user_id, test_property.id, test_call_session.id
    )
    assert "don't have verified information" in first
    gap = (await db_session.scalars(select(UnansweredQuestion))).one()

    await faq_service.answer_faq_gap(db_session, gap, "Yes, a small gym on the ground floor.", True, "host_gap_answer")
    kb_retrieval.invalidate_fact_cache()

    second = await tool_handlers.handle_search_faq(db_session, args, test_property.user_id, test_property.id)
    assert "small gym on the ground floor" in second


async def test_pending_faq_entries_are_never_served(db_session, test_property, test_user):
    db_session.add(
        FaqEntry(user_id=test_user.id, property_id=test_property.id, question="Gym?", answer="Unverified gym claim.", status="pending")
    )
    await db_session.commit()
    kb_retrieval.invalidate_fact_cache()
    text = await tool_handlers.handle_search_faq(
        db_session, SearchFaqArgs(query="gym", property_id=str(test_property.id)), test_user.id, test_property.id
    )
    assert "Unverified gym claim" not in text
