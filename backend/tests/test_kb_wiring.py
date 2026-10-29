"""End-to-end wiring of the knowledge-base grounding feature: the voice tools
feeding the guard, the WhatsApp check, the pipeline's guard builder, and the
write paths (property PATCH, FAQ API) that keep fact rows in sync."""

import uuid

import pytest
from sqlalchemy import select

from app.models.notification import Notification
from app.models.unanswered_question import UnansweredQuestion
from app.prompts.system_prompt import KB_MISS_LINE
from app.services import embedding_service
from app.services.property import kb_retrieval
from app.voice.conversation_state import ConversationState
from app.voice.kb_grounding_guard import KnowledgeGroundingGuardProcessor
from app.voice.tools import build_voice_tools
from tests.test_voice_tools import _FakeFunctionCallParams


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    kb_retrieval.invalidate_fact_cache()

    async def _none(text):
        return None

    monkeypatch.setattr(embedding_service, "get_embedding", _none)
    yield
    kb_retrieval.invalidate_fact_cache()


def _guard(mode="enforce") -> KnowledgeGroundingGuardProcessor:
    return KnowledgeGroundingGuardProcessor(mode=mode, miss_line=KB_MISS_LINE)


def _tool(tools, name):
    return next(t for t in tools if t.__name__ == name)


# --- tools feed the guard ----------------------------------------------------


async def test_search_faq_hit_feeds_facts_into_the_guard_corpus(test_property, test_user, db_session):
    test_property.neighborhood_info = "Mall Road is a 10 minute drive away."
    await db_session.commit()
    guard = _guard()
    tools = build_voice_tools(None, test_property.id, test_user.id, kb_grounding_guard=guard)

    params = _FakeFunctionCallParams()
    await _tool(tools, "search_faq")(params, query="how far is Mall Road")

    assert "10 minute drive" in params.result
    assert ("time", 10.0) in guard.corpus.measures
    assert guard._last_search_status == "found"
    # ...and the guard now accepts exactly that claim, but not an invented one.
    assert guard.check_outgoing("Mall Road is a ten minute drive.") == []
    assert guard.check_outgoing("Mall Road is a two hour drive.") != []


async def test_search_faq_miss_tells_guard_nothing_is_supported(test_property, test_user, db_session):
    guard = _guard()
    tools = build_voice_tools(None, test_property.id, test_user.id, kb_grounding_guard=guard)

    params = _FakeFunctionCallParams()
    await _tool(tools, "search_faq")(params, query="How far is Delhi?")

    assert guard._last_search_status == "none"
    assert "6 hours" not in params.result
    assert guard.check_outgoing("It's about 6 hours from Delhi.") != []


async def test_other_tool_results_become_support_for_later_claims(test_property, test_user, db_session):
    from datetime import date, timedelta

    guard = _guard()
    tools = build_voice_tools(None, test_property.id, test_user.id, kb_grounding_guard=guard)
    today = date.today()

    await _tool(tools, "check_calendar")(
        _FakeFunctionCallParams(),
        property_id=str(test_property.id),
        check_in=(today + timedelta(days=10)).isoformat(),
        check_out=(today + timedelta(days=12)).isoformat(),
    )

    assert any("AVAILABLE" in text for text in guard.corpus._tools)
    # search_faq's own instruction text must NOT be treated as support.
    await _tool(tools, "search_faq")(_FakeFunctionCallParams(), query="How far is Delhi?")
    assert not any("KNOWLEDGE BOUNDARY" in text for text in guard.corpus._tools)


async def test_tools_work_without_a_guard(test_property, test_user, db_session):
    tools = build_voice_tools(None, test_property.id, test_user.id)
    params = _FakeFunctionCallParams()
    await _tool(tools, "search_faq")(params, query="Is wifi free?")
    assert "Yes." in params.result


# --- send_whatsapp ----------------------------------------------------------


async def _whatsapp(tools, message):
    params = _FakeFunctionCallParams()
    await _tool(tools, "send_whatsapp")(params, message=message, phone="9876543210")
    return params


async def test_enforce_blocks_an_unsupported_whatsapp_and_sends_nothing(test_property, test_user, db_session):
    guard = _guard("enforce")
    tools = build_voice_tools(None, test_property.id, test_user.id, kb_grounding_guard=guard)

    params = await _whatsapp(tools, "Your villa is 6 hours from Delhi.")

    assert params.envelope["status"] == "business_error"
    assert "not on file" in params.result.lower() or "not on file" in str(params.envelope).lower()
    assert (await db_session.scalars(select(Notification))).all() == []


async def test_enforce_allows_a_supported_whatsapp(test_property, test_user, db_session):
    guard = _guard("enforce")
    guard.record_search("found", ["Check-in time is 14:00. Check-out time is 11:00."])
    tools = build_voice_tools(None, test_property.id, test_user.id, kb_grounding_guard=guard)

    params = await _whatsapp(tools, "Check-in is at 2 PM and check-out is at 11 AM.")

    assert params.envelope["status"] == "ok"
    assert len((await db_session.scalars(select(Notification))).all()) == 1


async def test_shadow_mode_does_not_block_a_whatsapp(test_property, test_user, db_session):
    guard = _guard("shadow")
    tools = build_voice_tools(None, test_property.id, test_user.id, kb_grounding_guard=guard)

    params = await _whatsapp(tools, "Your villa is 6 hours from Delhi.")

    assert params.envelope["status"] == "ok"
    assert len((await db_session.scalars(select(Notification))).all()) == 1


# --- pipeline guard builder --------------------------------------------------


async def test_pipeline_builds_a_guard_with_static_text_and_a_working_gap_sink(
    test_property, test_user, test_call_session, db_session
):
    from app.voice.pipeline import _build_kb_grounding_guard

    guard = await _build_kb_grounding_guard(
        host_user_id=test_user.id,
        property_id=test_property.id,
        call_session_id=test_call_session.id,
        guest_known_name="Priya",
        conversation_state=ConversationState(),
        context_holder={},
    )

    assert "Test Villa" in guard.corpus._static[0] and "Goa" in guard.corpus._static[0]
    assert "Priya" in guard.corpus._static[0]
    assert guard.check_outgoing("Check-in is at 2 PM.") == []  # 14:00 is the property's own default

    await guard._gap_sink("How far is Delhi?", "guard_blocked")
    gap = (await db_session.scalars(select(UnansweredQuestion))).one()
    assert (gap.property_id, gap.call_session_id, gap.source) == (test_property.id, test_call_session.id, "guard_blocked")


async def test_pipeline_guard_for_lead_agent_covers_the_whole_portfolio(test_property, test_user, test_call_session):
    from app.voice.pipeline import _build_kb_grounding_guard

    guard = await _build_kb_grounding_guard(
        host_user_id=test_user.id,
        property_id=None,
        call_session_id=test_call_session.id,
        guest_known_name=None,
        conversation_state=ConversationState(),
        context_holder={},
    )
    assert "Test Villa" in guard.corpus._static[0]


async def test_gap_sink_attributes_the_gap_to_the_property_selected_mid_call(
    test_property, test_user, test_call_session, db_session
):
    from app.voice.pipeline import _build_kb_grounding_guard

    state = ConversationState()
    state.selected_property_id = str(test_property.id)
    guard = await _build_kb_grounding_guard(
        host_user_id=test_user.id,
        property_id=None,  # Lead Agent call: no fixed property
        call_session_id=test_call_session.id,
        guest_known_name=None,
        conversation_state=state,
        context_holder={},
    )
    await guard._gap_sink("Is there a gym?", "miss_line_spoken")
    gap = (await db_session.scalars(select(UnansweredQuestion))).one()
    assert gap.property_id == test_property.id


# --- write paths keep the facts in sync -------------------------------------


async def _ask(db_session, test_property, test_user, query):
    from app.schemas.tool import SearchFaqArgs
    from app.services import tool_handlers

    return await tool_handlers.handle_search_faq(
        db_session, SearchFaqArgs(query=query, property_id=str(test_property.id)), test_user.id, test_property.id
    )


async def test_patching_a_property_makes_the_new_text_answerable_immediately(
    client, auth_headers, test_property, test_user, db_session
):
    before = await _ask(db_session, test_property, test_user, "bonfire")
    assert "don't have verified information" in before

    resp = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"house_rules": "Bonfire allowed on the terrace until 11 PM."},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    after = await _ask(db_session, test_property, test_user, "bonfire")
    assert "Bonfire allowed on the terrace" in after


async def test_patching_a_non_kb_field_does_not_rebuild_facts(
    client, auth_headers, test_property, test_user, db_session, monkeypatch
):
    from app.api.v1 import properties as properties_api

    calls = []

    async def spy(db, property_):
        calls.append(property_.id)

    monkeypatch.setattr(properties_api, "sync_property_facts", spy)
    await client.patch(f"/api/v1/properties/{test_property.id}", json={"base_price": 6100}, headers=auth_headers)
    assert calls == []
    await client.patch(f"/api/v1/properties/{test_property.id}", json={"usp": "New tagline."}, headers=auth_headers)
    assert calls == [test_property.id]


async def _persisted_fact_texts(db_session, property_id) -> list[str]:
    from app.models.property_chunk import PropertyChunk

    rows = await db_session.scalars(
        select(PropertyChunk).where(PropertyChunk.property_id == property_id, PropertyChunk.chunk_type == "fact")
    )
    return [row.text for row in rows]


async def test_faq_api_create_update_delete_keep_facts_in_sync(
    client, auth_headers, test_property, test_user, db_session
):
    created = await client.post(
        "/api/v1/faq",
        json={
            "property_id": str(test_property.id),
            "question": "Is there a gym?",
            "answer": "A small gym on the ground floor.",
            "status": "verified",
        },
        headers=auth_headers,
    )
    faq_id = created.json()["id"]
    # The sync hook itself (not just the staleness fallback) wrote the fact row.
    assert any("small gym on the ground floor" in t for t in await _persisted_fact_texts(db_session, test_property.id))
    assert "small gym on the ground floor" in await _ask(db_session, test_property, test_user, "gym")

    await client.patch(f"/api/v1/faq/{faq_id}", json={"answer": "A large gym on the roof."}, headers=auth_headers)
    updated = await _ask(db_session, test_property, test_user, "gym")
    assert "large gym on the roof" in updated and "small gym" not in updated

    await client.patch(f"/api/v1/faq/{faq_id}", json={"status": "pending"}, headers=auth_headers)
    assert "large gym" not in await _ask(db_session, test_property, test_user, "gym")

    await client.patch(f"/api/v1/faq/{faq_id}", json={"status": "verified"}, headers=auth_headers)
    assert "large gym on the roof" in await _ask(db_session, test_property, test_user, "gym")

    await client.delete(f"/api/v1/faq/{faq_id}", headers=auth_headers)
    assert "large gym" not in await _ask(db_session, test_property, test_user, "gym")
    assert not any("large gym" in t for t in await _persisted_fact_texts(db_session, test_property.id))


async def test_moving_a_faq_between_properties_removes_it_from_the_old_one(
    client, auth_headers, test_property, test_user, db_session
):
    from app.models.property import Property

    other = Property(user_id=test_user.id, name="Other Villa", city="Goa", base_price=3000, max_guests=2)
    db_session.add(other)
    await db_session.commit()
    created = await client.post(
        "/api/v1/faq",
        json={"property_id": str(test_property.id), "question": "Gym?", "answer": "Rooftop gym.", "status": "verified"},
        headers=auth_headers,
    )
    assert "Rooftop gym" in await _ask(db_session, test_property, test_user, "gym")

    await client.patch(f"/api/v1/faq/{created.json()['id']}", json={"property_id": str(other.id)}, headers=auth_headers)

    assert "Rooftop gym" not in await _ask(db_session, test_property, test_user, "gym")
    assert "Rooftop gym" in await _ask(db_session, other, test_user, "gym")


async def test_portfolio_wide_faq_reaches_every_property(client, auth_headers, test_property, test_user, db_session):
    await client.post(
        "/api/v1/faq",
        json={"question": "What is the cancellation policy?", "answer": "Free until 7 days before.", "status": "verified"},
        headers=auth_headers,
    )
    assert "Free until 7 days before" in await _ask(db_session, test_property, test_user, "cancellation policy")
