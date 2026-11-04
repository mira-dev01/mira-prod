"""Applying one FAQ answer to all properties or to a chosen set of them
(faq_entries.group_id; services/faq_service.py set_entry_scope)."""

import uuid

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.faq_entry import FaqEntry
from app.models.property import Property
from app.models.unanswered_question import UnansweredQuestion
from app.models.user import User
from app.schemas.tool import SearchFaqArgs
from app.services import embedding_service, tool_handlers
from app.services.property import kb_retrieval
from tests.conftest import auth_headers_for


@pytest.fixture(autouse=True)
def _no_embedding_api(monkeypatch):
    async def _none(text):
        return None

    monkeypatch.setattr(embedding_service, "get_embedding", _none)


async def _property(db, user, name):
    p = Property(user_id=user.id, name=name, city="Goa", base_price=3000, max_guests=2)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


@pytest.fixture
async def props(db_session, test_user, test_property):
    return [test_property, await _property(db_session, test_user, "Villa B"), await _property(db_session, test_user, "Villa C")]


async def _gap(db, user, property_id, question="Is there a gym?"):
    row = UnansweredQuestion(
        user_id=user.id, property_id=property_id, question=question, normalized_question=question.lower()
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _rows(db, user, question):
    # A fresh session: the API wrote through its own, and db_session's open
    # snapshot would not necessarily see those rows.
    async with AsyncSessionLocal() as fresh:
        return list(
            (await fresh.scalars(select(FaqEntry).where(FaqEntry.user_id == user.id, FaqEntry.question == question))).all()
        )


async def _search(db, user, property_, query):
    kb_retrieval.invalidate_fact_cache()
    async with AsyncSessionLocal() as fresh:
        return await tool_handlers.handle_search_faq(
            fresh, SearchFaqArgs(query=query, property_id=str(property_.id)), user.id, property_.id
        )


async def _gap_status(gap_id):
    async with AsyncSessionLocal() as fresh:
        return (await fresh.get(UnansweredQuestion, gap_id)).status


async def test_gap_answer_to_selected_properties_creates_one_grouped_row_each(
    client, auth_headers, test_user, props, db_session
):
    a, b, c = props
    gap = await _gap(db_session, test_user, a.id)
    response = await client.post(
        f"/api/v1/faq/gaps/{gap.id}/answer",
        headers=auth_headers,
        json={"answer": "A small gym on the ground floor.", "property_ids": [str(a.id), str(b.id)]},
    )
    assert response.status_code == 201, response.text

    rows = await _rows(db_session, test_user, "Is there a gym?")
    assert {r.property_id for r in rows} == {a.id, b.id}
    assert len({r.group_id for r in rows}) == 1 and rows[0].group_id is not None
    assert all(r.status == "verified" for r in rows)

    assert "small gym" in await _search(db_session, test_user, a, "gym")
    assert "small gym" in await _search(db_session, test_user, b, "gym")
    assert "small gym" not in await _search(db_session, test_user, c, "gym")

    assert await _gap_status(gap.id) == "answered"


async def test_gap_answer_apply_to_all_is_one_portfolio_row(client, auth_headers, test_user, props, db_session):
    gap = await _gap(db_session, test_user, props[0].id)
    response = await client.post(
        f"/api/v1/faq/gaps/{gap.id}/answer",
        headers=auth_headers,
        json={"answer": "Pets are welcome.", "apply_to_all": True, "question": "Are pets allowed?"},
    )
    assert response.status_code == 201
    rows = await _rows(db_session, test_user, "Are pets allowed?")
    assert len(rows) == 1 and rows[0].property_id is None and rows[0].group_id is None


async def test_gap_answer_rejects_a_property_the_host_does_not_own(
    client, auth_headers, test_user, props, db_session
):
    stranger = User(email=f"x-{uuid.uuid4().hex[:6]}@example.com", clerk_user_id=f"user_{uuid.uuid4().hex[:12]}")
    db_session.add(stranger)
    await db_session.commit()
    foreign = await _property(db_session, stranger, "Not Mine")

    gap = await _gap(db_session, test_user, props[0].id)
    response = await client.post(
        f"/api/v1/faq/gaps/{gap.id}/answer",
        headers=auth_headers,
        json={"answer": "x", "property_ids": [str(props[0].id), str(foreign.id)]},
    )
    assert response.status_code == 404
    assert await _rows(db_session, test_user, "Is there a gym?") == []
    assert await _gap_status(gap.id) == "pending"


async def _make_entry(client, headers, property_id, question="Is parking free?"):
    r = await client.post(
        "/api/v1/faq",
        headers=headers,
        json={"property_id": str(property_id), "question": question, "answer": "Yes, free parking.", "status": "verified"},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_widening_then_narrowing_scope(client, auth_headers, test_user, props, db_session):
    a, b, c = props
    entry = await _make_entry(client, auth_headers, a.id)
    assert entry["group_id"] is None

    widened = await client.put(
        f"/api/v1/faq/{entry['id']}/scope",
        headers=auth_headers,
        json={"property_ids": [str(a.id), str(b.id), str(c.id)]},
    )
    assert widened.status_code == 200, widened.text
    rows = widened.json()
    assert {r["property_id"] for r in rows} == {str(a.id), str(b.id), str(c.id)}
    assert len({r["group_id"] for r in rows}) == 1 and rows[0]["group_id"] is not None
    assert entry["id"] in {r["id"] for r in rows}  # the original row was kept, not recreated
    assert "free parking" in await _search(db_session, test_user, c, "parking")

    narrowed = await client.put(
        f"/api/v1/faq/{entry['id']}/scope", headers=auth_headers, json={"property_ids": [str(b.id)]}
    )
    rows = narrowed.json()
    assert [r["property_id"] for r in rows] == [str(b.id)]
    assert rows[0]["group_id"] is None  # a single row is standalone again
    assert len(await _rows(db_session, test_user, "Is parking free?")) == 1
    assert "free parking" not in await _search(db_session, test_user, a, "parking")
    assert "free parking" in await _search(db_session, test_user, b, "parking")


async def test_scope_to_all_collapses_to_one_portfolio_row(client, auth_headers, test_user, props, db_session):
    a, b, _ = props
    entry = await _make_entry(client, auth_headers, a.id)
    await client.put(
        f"/api/v1/faq/{entry['id']}/scope", headers=auth_headers, json={"property_ids": [str(a.id), str(b.id)]}
    )
    rows = (await client.put(f"/api/v1/faq/{entry['id']}/scope", headers=auth_headers, json={"apply_to_all": True})).json()
    assert len(rows) == 1 and rows[0]["property_id"] is None and rows[0]["group_id"] is None
    assert len(await _rows(db_session, test_user, "Is parking free?")) == 1


async def test_scope_requires_a_choice_and_an_owned_entry(client, auth_headers, test_user, props, db_session):
    entry = await _make_entry(client, auth_headers, props[0].id)
    empty = await client.put(f"/api/v1/faq/{entry['id']}/scope", headers=auth_headers, json={})
    assert empty.status_code == 422

    stranger = User(email=f"y-{uuid.uuid4().hex[:6]}@example.com", clerk_user_id=f"user_{uuid.uuid4().hex[:12]}")
    db_session.add(stranger)
    await db_session.commit()
    other = await client.put(
        f"/api/v1/faq/{entry['id']}/scope", headers=auth_headers_for(stranger), json={"apply_to_all": True}
    )
    assert other.status_code == 404


async def test_editing_a_grouped_faq_updates_every_property_copy(client, auth_headers, test_user, props, db_session):
    a, b, _ = props
    entry = await _make_entry(client, auth_headers, a.id)
    await client.put(
        f"/api/v1/faq/{entry['id']}/scope", headers=auth_headers, json={"property_ids": [str(a.id), str(b.id)]}
    )

    edited = await client.patch(
        f"/api/v1/faq/{entry['id']}", headers=auth_headers, json={"answer": "Parking costs ₹100 a night."}
    )
    assert edited.status_code == 200
    rows = await _rows(db_session, test_user, "Is parking free?")
    assert len(rows) == 2 and {r.answer for r in rows} == {"Parking costs ₹100 a night."}
    assert "₹100" in await _search(db_session, test_user, b, "parking")

    moved = await client.patch(
        f"/api/v1/faq/{entry['id']}", headers=auth_headers, json={"property_id": str(props[2].id)}
    )
    assert moved.status_code == 400


async def test_delete_removes_one_row_or_the_whole_group(client, auth_headers, test_user, props, db_session):
    a, b, _ = props
    entry = await _make_entry(client, auth_headers, a.id)
    await client.put(
        f"/api/v1/faq/{entry['id']}/scope", headers=auth_headers, json={"property_ids": [str(a.id), str(b.id)]}
    )
    assert (await client.delete(f"/api/v1/faq/{entry['id']}", headers=auth_headers)).status_code == 204
    assert len(await _rows(db_session, test_user, "Is parking free?")) == 1

    entry2 = await _make_entry(client, auth_headers, a.id, question="Is there a lift?")
    await client.put(
        f"/api/v1/faq/{entry2['id']}/scope", headers=auth_headers, json={"property_ids": [str(a.id), str(b.id)]}
    )
    assert (await client.delete(f"/api/v1/faq/{entry2['id']}?group=true", headers=auth_headers)).status_code == 204
    assert await _rows(db_session, test_user, "Is there a lift?") == []


async def test_create_with_several_properties(client, auth_headers, test_user, props, db_session):
    a, b, _ = props
    r = await client.post(
        "/api/v1/faq",
        headers=auth_headers,
        json={
            "property_ids": [str(a.id), str(b.id)],
            "question": "Is there a kettle?",
            "answer": "Yes.",
            "status": "verified",
        },
    )
    assert r.status_code == 201, r.text
    rows = await _rows(db_session, test_user, "Is there a kettle?")
    assert {x.property_id for x in rows} == {a.id, b.id} and len({x.group_id for x in rows}) == 1


async def test_rescoping_removes_a_stray_duplicate_row_instead_of_orphaning_it(
    client, auth_headers, test_user, props, db_session
):
    a, b, _ = props
    entry = await _make_entry(client, auth_headers, a.id, question="Is there a gym nearby?")
    await client.put(
        f"/api/v1/faq/{entry['id']}/scope", headers=auth_headers, json={"property_ids": [str(a.id), str(b.id)]}
    )
    rows = await _rows(db_session, test_user, "Is there a gym nearby?")
    group_id = rows[0].group_id
    async with AsyncSessionLocal() as fresh:  # a second row for property A in the same group
        fresh.add(
            FaqEntry(
                user_id=test_user.id, property_id=a.id, group_id=group_id,
                question="Is there a gym nearby?", answer="Yes, free parking.", status="verified",
            )
        )
        await fresh.commit()
    assert len(await _rows(db_session, test_user, "Is there a gym nearby?")) == 3

    result = await client.put(
        f"/api/v1/faq/{entry['id']}/scope", headers=auth_headers, json={"property_ids": [str(a.id), str(b.id)]}
    )
    assert result.status_code == 200
    assert len(await _rows(db_session, test_user, "Is there a gym nearby?")) == 2  # one per property again
