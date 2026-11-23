import uuid

from app.models.unanswered_question import UnansweredQuestion
from app.services import faq_service


async def _add_gap(db_session, user_id, property_id, question, call_session_id=None):
    row = UnansweredQuestion(
        user_id=user_id,
        property_id=property_id,
        call_session_id=call_session_id,
        question=question,
        normalized_question=question.strip().lower(),
    )
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    return row


async def test_list_faq_gaps_groups_by_normalized_question_and_ranks_by_frequency(
    test_user, test_property, db_session
):
    await _add_gap(db_session, test_user.id, test_property.id, "Is breakfast included?")
    await _add_gap(db_session, test_user.id, test_property.id, "is BREAKFAST included?")
    await _add_gap(db_session, test_user.id, test_property.id, "Is breakfast included?")
    await _add_gap(db_session, test_user.id, test_property.id, "Do you have parking?")

    gaps = await faq_service.list_faq_gaps(db_session, test_user.id)

    assert len(gaps) == 2
    assert gaps[0].normalized_question == "is breakfast included?"
    assert gaps[0].count == 3
    assert gaps[1].count == 1


async def test_list_faq_gaps_excludes_answered_rows(test_user, test_property, db_session):
    gap = await _add_gap(db_session, test_user.id, test_property.id, "Is there a gym?")
    await faq_service.answer_faq_gap(db_session, gap, "No, but there's a park nearby.", False, "host_gap_answer")

    gaps = await faq_service.list_faq_gaps(db_session, test_user.id)
    assert gaps == []


async def test_answer_faq_gap_creates_verified_entry_and_clears_whole_group(
    test_user, test_property, db_session
):
    gap1 = await _add_gap(db_session, test_user.id, test_property.id, "Is breakfast included?")
    await _add_gap(db_session, test_user.id, test_property.id, "is breakfast included?")

    entry = await faq_service.answer_faq_gap(
        db_session, gap1, "No, but we can arrange it for ₹450/person/day.", False, "host_gap_answer"
    )

    assert entry.status == "verified"
    assert entry.verified_by == "host_gap_answer"
    assert entry.property_id is None  # apply_to_property=False -> portfolio-wide

    remaining = await faq_service.list_faq_gaps(db_session, test_user.id)
    assert remaining == []


async def test_answer_faq_gap_apply_to_property_scopes_the_new_entry(test_user, test_property, db_session):
    gap = await _add_gap(db_session, test_user.id, test_property.id, "Is there a caretaker?")
    entry = await faq_service.answer_faq_gap(db_session, gap, "Yes, on-site 9am-6pm.", True, "host_gap_answer")
    assert entry.property_id == test_property.id


async def test_faq_gap_analytics_breakdowns(test_user, test_property, db_session):
    await _add_gap(db_session, test_user.id, test_property.id, "Is breakfast included?")
    await _add_gap(db_session, test_user.id, test_property.id, "Is breakfast included?")
    await _add_gap(db_session, test_user.id, None, "Do you allow pets?")

    analytics = await faq_service.faq_gap_analytics(db_session, test_user.id)

    assert analytics["most_frequent"][0]["count"] == 2
    property_counts = {row["property_id"]: row["count"] for row in analytics["by_property"]}
    assert property_counts[test_property.id] == 2
    assert property_counts[None] == 1
    assert len(analytics["over_time"]) >= 1


async def test_get_owned_unanswered_question_enforces_ownership(test_user, test_property, db_session):
    gap = await _add_gap(db_session, test_user.id, test_property.id, "Is there a gym?")

    other_user_id = uuid.uuid4()
    assert await faq_service.get_owned_unanswered_question(db_session, gap.id, other_user_id) is None
    assert await faq_service.get_owned_unanswered_question(db_session, gap.id, test_user.id) is not None


async def test_faq_gaps_api_list_and_answer(client, auth_headers, test_user, test_property, db_session):
    await _add_gap(db_session, test_user.id, test_property.id, "Is breakfast included?")

    res = await client.get("/api/v1/faq/gaps", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    gap_id = body[0]["sample_id"]

    res = await client.post(
        f"/api/v1/faq/gaps/{gap_id}/answer",
        headers=auth_headers,
        json={"answer": "No, but breakfast can be arranged for ₹450/person/day.", "apply_to_property": False},
    )
    assert res.status_code == 201
    assert res.json()["status"] == "verified"

    res = await client.get("/api/v1/faq/gaps", headers=auth_headers)
    assert res.json() == []


async def test_gap_source_is_exposed_through_the_api(client, auth_headers, test_user, test_property, db_session):
    row = await _add_gap(db_session, test_user.id, test_property.id, "How far is Delhi?")
    row.source = "guard_blocked"
    await db_session.commit()

    response = await client.get("/api/v1/faq/gaps", headers=auth_headers)

    assert response.status_code == 200
    gap = next(g for g in response.json() if g["question"] == "How far is Delhi?")
    assert gap["source"] == "guard_blocked"
    assert gap["property_id"] == str(test_property.id)


async def test_property_scoped_gap_answer_becomes_a_fact_for_that_property_only(
    test_user, test_property, db_session
):
    """The dashboard answers a gap 'for this property only' -- the answer must
    be searchable for that property and must not leak to another."""
    from app.models.property import Property
    from app.schemas.tool import SearchFaqArgs
    from app.services import embedding_service, tool_handlers
    from app.services.property import kb_retrieval

    async def _none(text):
        return None

    embedding_service.get_embedding, original = _none, embedding_service.get_embedding
    try:
        other = Property(user_id=test_user.id, name="Other Villa", city="Goa", base_price=3000, max_guests=2)
        db_session.add(other)
        await db_session.commit()

        gap = await _add_gap(db_session, test_user.id, test_property.id, "Is there a gym?")
        await faq_service.answer_faq_gap(db_session, gap, "A small gym on the ground floor.", True, "host_gap_answer")
        kb_retrieval.invalidate_fact_cache()

        mine = await tool_handlers.handle_search_faq(
            db_session, SearchFaqArgs(query="gym", property_id=str(test_property.id)), test_user.id, test_property.id
        )
        theirs = await tool_handlers.handle_search_faq(
            db_session, SearchFaqArgs(query="gym", property_id=str(other.id)), test_user.id, other.id
        )
    finally:
        embedding_service.get_embedding = original

    assert "small gym on the ground floor" in mine
    assert "small gym" not in theirs


# -- what is (and is not) a knowledge gap --------------------------------------

import asyncio

import pytest
from sqlalchemy import select

from app.models.call_session import CallSession
from app.models.ticket import Ticket
from app.models.user import User
from app.services import kb_gap_service, tool_handlers
from app.utils.throttle import Throttle


@pytest.mark.parametrize(
    "question",
    [
        "What?",
        "Sorry, I didn't quite catch that -- could you say that again?",
        "I told you know next weekend Saturday.",
        "okay",
        "",
    ],
)
def test_filler_is_never_a_gap(question):
    assert kb_gap_service.classify_gap(question) == "skip"


@pytest.mark.parametrize(
    "question",
    [
        "payment link",
        "What is the price for this?",
        "availability December 19 2026",
        "Can you check the Soul Club for 19 December?",
    ],
)
def test_availability_price_and_payment_asks_are_requests_not_faqs(question):
    assert kb_gap_service.classify_gap(question) == "request"


@pytest.mark.parametrize(
    "question",
    ["Is breakfast included?", "nearby attractions", "Indra Nagar property", "what is the refund policy", "Is the pool heated?"],
)
def test_property_questions_stay_knowledge_gaps(question):
    assert kb_gap_service.classify_gap(question) == "knowledge"


@pytest.fixture
def request_emails(monkeypatch):
    sent = []

    async def email(to, subject, body, html_body):
        sent.append((to, subject, body))

    monkeypatch.setattr(tool_handlers, "_send_escalation_email", email)
    monkeypatch.setattr(kb_gap_service, "_request_email_throttle", Throttle(kb_gap_service._request_email_throttle._window))
    return sent


async def _call(db_session, user_id, property_id):
    call = CallSession(exotel_call_id=f"call-{uuid.uuid4().hex[:8]}", user_id=user_id, property_id=property_id, caller_number="+919876543210")
    db_session.add(call)
    await db_session.commit()
    await db_session.refresh(call)
    return call


async def _tickets_of(db_session, user_id):
    return list((await db_session.scalars(select(Ticket).where(Ticket.user_id == user_id))).all())


async def test_a_payment_link_ask_raises_a_ticket_and_emails_the_host_not_an_faq_gap(
    request_emails, test_user, test_property, db_session
):
    call = await _call(db_session, test_user.id, test_property.id)

    result = await kb_gap_service.log_kb_gap(
        db_session, user_id=test_user.id, property_id=test_property.id, call_session_id=call.id, question="payment link"
    )
    await asyncio.sleep(0.05)  # the email is sent detached

    assert result is None
    assert await faq_service.list_faq_gaps(db_session, test_user.id) == []
    tickets = await _tickets_of(db_session, test_user.id)
    assert [t.kind for t in tickets] == ["request"] and tickets[0].status == "open"
    assert len(request_emails) == 1 and request_emails[0][0] == test_user.email
    assert "payment link" in request_emails[0][2]


async def test_the_same_ask_twice_in_one_call_is_one_ticket_and_one_email(request_emails, test_user, test_property, db_session):
    call = await _call(db_session, test_user.id, test_property.id)
    for _ in range(2):
        await kb_gap_service.log_kb_gap(
            db_session, user_id=test_user.id, property_id=test_property.id, call_session_id=call.id, question="What is the price for this?"
        )
    await asyncio.sleep(0.05)
    assert len(await _tickets_of(db_session, test_user.id)) == 1
    assert len(request_emails) == 1


async def test_a_host_with_escalation_email_off_still_gets_the_ticket(request_emails, test_user, test_property, db_session):
    user = await db_session.get(User, test_user.id)
    user.notification_preferences = {"escalation_email": False}
    await db_session.commit()

    await kb_gap_service.log_kb_gap(
        db_session, user_id=test_user.id, property_id=test_property.id, call_session_id=None, question="availability December 19 2026"
    )
    await asyncio.sleep(0.05)

    assert len(await _tickets_of(db_session, test_user.id)) == 1
    assert request_emails == []


async def test_filler_logs_nothing_and_a_property_question_still_becomes_a_gap(request_emails, test_user, test_property, db_session):
    for question in ("What?", "Is the pool heated?"):
        await kb_gap_service.log_kb_gap(
            db_session, user_id=test_user.id, property_id=test_property.id, call_session_id=None, question=question
        )
    gaps = await faq_service.list_faq_gaps(db_session, test_user.id)
    assert [g.question for g in gaps] == ["Is the pool heated?"]
    assert await _tickets_of(db_session, test_user.id) == [] and request_emails == []


async def test_dismissing_a_gap_clears_its_whole_group_and_only_for_the_owner(
    client, auth_headers, test_user, test_property, db_session
):
    await _add_gap(db_session, test_user.id, test_property.id, "Some stray question")
    await _add_gap(db_session, test_user.id, test_property.id, "some stray QUESTION")
    gap_id = (await client.get("/api/v1/faq/gaps", headers=auth_headers)).json()[0]["sample_id"]

    res = await client.post(f"/api/v1/faq/gaps/{gap_id}/dismiss", headers=auth_headers)

    assert res.status_code == 204
    assert (await client.get("/api/v1/faq/gaps", headers=auth_headers)).json() == []
    assert (await client.post(f"/api/v1/faq/gaps/{uuid.uuid4()}/dismiss", headers=auth_headers)).status_code == 404
