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
