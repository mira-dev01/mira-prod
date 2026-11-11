"""Policies waiting on the host for a property: a policy that names a villa must
reach THAT villa only, a name the dashboard could not map is flagged (never
guessed), and a property that is created, renamed or deleted re-derives who
follows what. Real Postgres; embeddings stubbed."""

import json
import uuid

import pytest
from sqlalchemy import select

from app.models.policy import Policy
from app.models.property_chunk import PropertyChunk
from app.services import embedding_service, policy_documents, policy_service
from app.services.policy_service import HANDLED_PREFIX, UNMATCHED_NOTE_MARK, PolicyError
from app.services.property import chunking, kb_retrieval
from tests.conftest import auth_headers_for
from tests.test_policies import _fact_texts, _host, _policy_facts, _property


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(embedding_service, "backfill_property_chunk_embeddings", _noop)
    monkeypatch.setattr(embedding_service, "get_embedding", _noop)
    kb_retrieval.invalidate_fact_cache()
    yield
    kb_retrieval.invalidate_fact_cache()


def _note(name: str) -> list[str]:
    return [f"The document mentions {name}, which {UNMATCHED_NOTE_MARK}."]


async def _placeholder(db, host, *, topic="pets", title="No Pets at Deccan Escape", text="Pets are not allowed at Deccan Escape.", name="Deccan Escape"):
    policy = Policy(user_id=host.id, topic=topic, title=title, text=text, status="active", review_notes=_note(name))
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return policy


async def _overview(db, host):
    return {p["name"]: p for p in (await policy_service.overview(db, host))["properties"]}


# -- the leak: a policy about one villa reaching every villa ---------------------


async def test_a_property_created_later_pulls_a_villa_policy_out_of_the_others(db_session):
    host = await _host(db_session)
    sea = await _property(db_session, host, "Sea View")
    hill = await _property(db_session, host, "Hill Cabin")
    # No property is called "Deccan Escape" yet, so the policy reads as being about everyone.
    pets = Policy(user_id=host.id, topic="pets", title="Pets", text="Pets are not allowed at Deccan Escape.", status="active", review_notes=[])
    db_session.add(pets)
    await db_session.commit()
    await policy_service.resync(db_session, host.id, None)
    assert _policy_facts(await _fact_texts(db_session, sea))  # stored on Sea View

    deccan = await _property(db_session, host, "Deccan Escape 4 BHK Luxury Stay")
    rebuilt = await policy_service.sync_policy_scopes(db_session, host.id)

    assert rebuilt >= 2  # Sea View and Hill Cabin were rebuilt, plus Deccan itself
    assert _policy_facts(await _fact_texts(db_session, sea)) == []
    assert _policy_facts(await _fact_texts(db_session, hill)) == []
    assert any("Deccan Escape" in t for t in _policy_facts(await _fact_texts(db_session, deccan)))


async def test_a_second_pass_changes_nothing(db_session):
    host = await _host(db_session)
    await _property(db_session, host, "Sea View")
    await policy_service.create_policy(db_session, host, topic="pets", text="Pets are welcome.")

    assert await policy_service.sync_policy_scopes(db_session, host.id) == 0


async def test_deleting_a_property_re_derives_the_others(db_session):
    host = await _host(db_session)
    sea = await _property(db_session, host, "Sea View")
    deccan = await _property(db_session, host, "Deccan Escape")
    db_session.add(Policy(user_id=host.id, topic="pets", title="Pets", text="Pets are not allowed at Deccan Escape.", status="active", review_notes=[]))
    await db_session.commit()
    await policy_service.sync_policy_scopes(db_session, host.id)
    assert _policy_facts(await _fact_texts(db_session, sea)) == []

    await db_session.delete(deccan)
    await db_session.commit()
    rebuilt = await policy_service.sync_policy_scopes(db_session, host.id)

    # Sea View was re-derived (with no property of that name left the sentence names nobody),
    # and the result is stable: the stored facts now match what the rules say.
    assert rebuilt == 1
    assert await policy_service.sync_policy_scopes(db_session, host.id) == 0


# -- placeholders are held until assigned -----------------------------------------


async def test_a_placeholder_is_spoken_for_nobody_until_assigned(db_session):
    host = await _host(db_session)
    deccan = await _property(db_session, host, "Deccan Escape 4 BHK Luxury Stay")
    sea = await _property(db_session, host, "Sea View")
    placeholder = await _placeholder(db_session, host)
    await policy_service.resync(db_session, host.id, None)

    assert _policy_facts(await _fact_texts(db_session, deccan)) == []
    assert _policy_facts(await _fact_texts(db_session, sea)) == []
    props = await _overview(db_session, host)
    assert props["Deccan Escape 4 BHK Luxury Stay"]["pending_policy_ids"] == [placeholder.id]
    assert props["Sea View"]["pending_policy_ids"] == []


async def test_assigning_makes_it_that_propertys_own_and_only_theirs(db_session):
    host = await _host(db_session)
    deccan = await _property(db_session, host, "Deccan Escape 4 BHK Luxury Stay")
    sea = await _property(db_session, host, "Sea View")
    placeholder = await _placeholder(db_session, host)

    scoped = await policy_service.assign_policy(db_session, host, placeholder.id, deccan.id)

    assert scoped.property_id == deccan.id and scoped.status == "active" and scoped.review_notes == []
    assert any("Pets are not allowed at Deccan Escape" in t for t in _policy_facts(await _fact_texts(db_session, deccan)))
    assert _policy_facts(await _fact_texts(db_session, sea)) == []
    assert await db_session.get(Policy, placeholder.id) is None  # nothing else to offer it to: it is gone


async def test_assigning_replaces_the_propertys_current_policy_for_that_topic(db_session):
    host = await _host(db_session)
    deccan = await _property(db_session, host, "Deccan Escape")
    old = await policy_service.create_policy(db_session, host, topic="pets", text="Pets are welcome.", property_id=deccan.id)
    placeholder = await _placeholder(db_session, host)

    await policy_service.assign_policy(db_session, host, placeholder.id, deccan.id)

    assert await db_session.get(Policy, old.id) is None
    facts = _policy_facts(await _fact_texts(db_session, deccan))
    assert len(facts) == 1 and "not allowed" in facts[0]


async def test_a_policy_naming_two_properties_stays_on_offer_for_the_other(db_session):
    host = await _host(db_session)
    deccan = await _property(db_session, host, "Deccan Escape")
    bombay = await _property(db_session, host, "Bombay Breeze")
    placeholder = await _placeholder(
        db_session, host, text="Pets are not allowed at Deccan Escape and Bombay Breeze.", title="No pets"
    )

    await policy_service.assign_policy(db_session, host, placeholder.id, deccan.id)

    still = await db_session.get(Policy, placeholder.id)
    assert still is not None and f"{HANDLED_PREFIX}{deccan.id}" in still.review_notes
    props = await _overview(db_session, host)
    assert props["Deccan Escape"]["pending_policy_ids"] == []
    assert props["Bombay Breeze"]["pending_policy_ids"] == [placeholder.id]

    await policy_service.assign_policy(db_session, host, placeholder.id, bombay.id)
    assert await db_session.get(Policy, placeholder.id) is None


async def test_deleting_for_one_property_leaves_it_on_offer_for_the_other(db_session):
    host = await _host(db_session)
    deccan = await _property(db_session, host, "Deccan Escape")
    await _property(db_session, host, "Bombay Breeze")
    placeholder = await _placeholder(
        db_session, host, text="Pets are not allowed at Deccan Escape and Bombay Breeze.", title="No pets"
    )

    await policy_service.dismiss_policy(db_session, host, placeholder.id, deccan.id)

    props = await _overview(db_session, host)
    assert props["Deccan Escape"]["pending_policy_ids"] == []
    assert props["Bombay Breeze"]["pending_policy_ids"] == [placeholder.id]
    assert _policy_facts(await _fact_texts(db_session, deccan)) == []  # not dropped onto it either


async def test_the_handled_markers_are_never_shown_to_the_host(db_session):
    host = await _host(db_session)
    deccan = await _property(db_session, host, "Deccan Escape")
    await _property(db_session, host, "Bombay Breeze")
    placeholder = await _placeholder(
        db_session, host, text="Pets are not allowed at Deccan Escape and Bombay Breeze.", title="No pets"
    )
    await policy_service.dismiss_policy(db_session, host, placeholder.id, deccan.id)

    overview = await policy_service.overview(db_session, host)
    shown = [p for row in overview["rows"] for p in row["items"] if p["id"] == placeholder.id][0]

    assert all(HANDLED_PREFIX not in note for note in shown["review_notes"])
    assert shown["review_notes"]  # the real flag is still there


async def test_a_placeholder_that_names_no_property_is_listed_as_unmapped(db_session):
    host = await _host(db_session)
    await _property(db_session, host, "Sea View")
    placeholder = await _placeholder(db_session, host, text="Pets are not allowed at Crystal Hall.", title="No pets", name="Crystal Hall")

    overview = await policy_service.overview(db_session, host)

    assert overview["unmapped_policy_ids"] == [placeholder.id]
    assert all(p["pending_policy_ids"] == [] for p in overview["properties"])


async def test_once_a_matching_property_is_added_the_placeholder_moves_from_unmapped_to_pending(db_session):
    host = await _host(db_session)
    await _property(db_session, host, "Sea View")
    placeholder = await _placeholder(db_session, host, text="Pets are not allowed at Crystal Hall.", title="No pets", name="Crystal Hall")
    crystal = await _property(db_session, host, "Crystal Hall 5BHK Spacez Modernistic Villa")

    overview = await policy_service.overview(db_session, host)

    assert overview["unmapped_policy_ids"] == []
    props = {p["name"]: p for p in overview["properties"]}
    assert props["Crystal Hall 5BHK Spacez Modernistic Villa"]["pending_policy_ids"] == [placeholder.id]
    assert crystal.id  # the placeholder is only offered, never applied on its own
    assert _policy_facts(await _fact_texts(db_session, crystal)) == []


# -- drafts ---------------------------------------------------------------------


async def test_a_property_draft_is_pending_for_that_property_and_approvable_from_its_pane(db_session):
    host = await _host(db_session)
    sea = await _property(db_session, host, "Sea View")
    draft = Policy(user_id=host.id, property_id=sea.id, topic="pets", title="Pets", text="Dogs welcome.", status="draft", review_notes=[])
    db_session.add(draft)
    await db_session.commit()

    props = await _overview(db_session, host)
    assert props["Sea View"]["pending_policy_ids"] == [draft.id]
    assert _policy_facts(await _fact_texts(db_session, sea)) == []  # a draft is never spoken

    approved = await policy_service.assign_policy(db_session, host, draft.id, sea.id)

    assert approved.status == "active"
    assert any("Dogs welcome" in t for t in _policy_facts(await _fact_texts(db_session, sea)))


async def test_deleting_a_property_draft_removes_it(db_session):
    host = await _host(db_session)
    sea = await _property(db_session, host, "Sea View")
    draft = Policy(user_id=host.id, property_id=sea.id, topic="pets", title="Pets", text="Dogs welcome.", status="draft", review_notes=[])
    db_session.add(draft)
    await db_session.commit()

    await policy_service.dismiss_policy(db_session, host, draft.id, sea.id)

    assert await db_session.get(Policy, draft.id) is None


async def test_a_host_wide_draft_that_names_a_property_is_offered_to_it(db_session):
    host = await _host(db_session)
    deccan = await _property(db_session, host, "Deccan Escape")
    sea = await _property(db_session, host, "Sea View")
    draft = Policy(user_id=host.id, topic="pets", title="Pets", text="Pets are not allowed at Deccan Escape.", status="draft", review_notes=[])
    db_session.add(draft)
    await db_session.commit()

    props = await _overview(db_session, host)
    assert props["Deccan Escape"]["pending_policy_ids"] == [draft.id]
    assert props["Sea View"]["pending_policy_ids"] == []

    await policy_service.assign_policy(db_session, host, draft.id, deccan.id)
    assert any("not allowed at Deccan Escape" in t for t in _policy_facts(await _fact_texts(db_session, deccan)))
    assert _policy_facts(await _fact_texts(db_session, sea)) == []


async def test_a_general_draft_is_not_pending_for_any_property(db_session):
    host = await _host(db_session)
    await _property(db_session, host, "Sea View")
    db_session.add(Policy(user_id=host.id, topic="smoking", title="Smoking", text="No smoking indoors.", status="draft", review_notes=[]))
    await db_session.commit()

    assert all(p["pending_policy_ids"] == [] for p in (await policy_service.overview(db_session, host))["properties"])


async def test_a_policy_that_is_not_waiting_cannot_be_assigned(db_session):
    host = await _host(db_session)
    sea = await _property(db_session, host, "Sea View")
    live = await policy_service.create_policy(db_session, host, topic="smoking", text="No smoking.")

    with pytest.raises(PolicyError):
        await policy_service.assign_policy(db_session, host, live.id, sea.id)
    with pytest.raises(PolicyError):
        await policy_service.dismiss_policy(db_session, host, live.id, sea.id)


async def test_another_hosts_property_or_policy_is_refused(db_session):
    mine, theirs = await _host(db_session, "Mine"), await _host(db_session, "Theirs")
    my_property = await _property(db_session, mine, "Sea View")
    their_property = await _property(db_session, theirs, "Hill Cabin")
    placeholder = await _placeholder(db_session, mine, text="Pets are not allowed at Sea View.", title="No pets", name="Sea View")

    with pytest.raises(PolicyError):
        await policy_service.assign_policy(db_session, mine, placeholder.id, their_property.id)
    with pytest.raises(PolicyError):
        await policy_service.assign_policy(db_session, theirs, placeholder.id, their_property.id)
    assert my_property.id


# -- reading a document ----------------------------------------------------------


def _llm(monkeypatch, items: list[dict]):
    async def fake(prompt):
        return json.dumps({"items": items})

    monkeypatch.setattr(policy_documents, "_call_text_llm", fake)


async def test_a_rule_that_names_a_property_in_its_text_is_mapped_even_if_the_reader_named_none(db_session, monkeypatch):
    host = await _host(db_session)
    deccan = await _property(db_session, host, "Deccan Escape 4 BHK Luxury Stay")
    await _property(db_session, host, "Sea View")
    _llm(monkeypatch, [{"topic": "pets", "text": "Pets are not allowed at Deccan Escape.", "title": "No pets", "excerpt": "Pets"}])

    _, drafts, _ = await policy_service.ingest_document(
        db_session, host, filename="t.txt", content_type=None, data=b"Pets are not allowed at Deccan Escape."
    )

    assert [d.property_id for d in drafts] == [deccan.id]


async def test_a_general_rule_the_reader_named_no_property_for_stays_host_wide(db_session, monkeypatch):
    host = await _host(db_session)
    await _property(db_session, host, "Sea View")
    _llm(monkeypatch, [{"topic": "smoking", "text": "Smoking is allowed inside the villa.", "title": "Smoking", "excerpt": "Smoking"}])

    _, drafts, _ = await policy_service.ingest_document(
        db_session, host, filename="t.txt", content_type=None, data=b"Smoking is allowed inside the villa."
    )

    assert [d.property_id for d in drafts] == [None]


# -- HTTP --------------------------------------------------------------------------


async def test_http_assign_and_dismiss(client, db_session):
    host = await _host(db_session)
    deccan = await _property(db_session, host, "Deccan Escape")
    bombay = await _property(db_session, host, "Bombay Breeze")
    placeholder = await _placeholder(
        db_session, host, text="Pets are not allowed at Deccan Escape and Bombay Breeze.", title="No pets"
    )
    headers = auth_headers_for(host)

    overview = (await client.get("/api/v1/policies", headers=headers)).json()
    pending = {p["name"]: p["pending_policy_ids"] for p in overview["properties"]}
    assert pending["Deccan Escape"] == [str(placeholder.id)]

    assigned = await client.post(f"/api/v1/policies/{placeholder.id}/assign", headers=headers, json={"property_id": str(deccan.id)})
    assert assigned.status_code == 200 and assigned.json()["property_id"] == str(deccan.id)

    dismissed = await client.post(f"/api/v1/policies/{placeholder.id}/dismiss", headers=headers, json={"property_id": str(bombay.id)})
    assert dismissed.status_code == 204

    overview = (await client.get("/api/v1/policies", headers=headers)).json()
    assert all(p["pending_policy_ids"] == [] for p in overview["properties"])
    rows = (await db_session.scalars(select(Policy).where(Policy.user_id == host.id))).all()
    assert [p.property_id for p in rows] == [deccan.id]  # the shared placeholder is gone, Deccan has its own


async def test_http_refuses_a_policy_that_is_not_waiting(client, db_session):
    host = await _host(db_session)
    sea = await _property(db_session, host, "Sea View")
    live = await policy_service.create_policy(db_session, host, topic="smoking", text="No smoking.")

    response = await client.post(f"/api/v1/policies/{live.id}/assign", headers=auth_headers_for(host), json={"property_id": str(sea.id)})

    assert response.status_code == 422


async def test_the_chunk_table_has_one_policy_ref_per_policy(db_session):
    # sync_policy_scopes compares stored policy refs with expected ones; they must be 'policy_<id>'.
    host = await _host(db_session)
    sea = await _property(db_session, host, "Sea View")
    policy = await policy_service.create_policy(db_session, host, topic="smoking", text="No smoking.")
    refs = set(
        (
            await db_session.scalars(
                select(PropertyChunk.source_ref).where(
                    PropertyChunk.property_id == sea.id, PropertyChunk.source_kind == "policy"
                )
            )
        ).all()
    )
    assert refs == {f"policy_{policy.id}"}
    assert chunking.FACT_CHUNK_TYPE == "fact"
    assert uuid.UUID(str(policy.id))


# -- the nightly audit -------------------------------------------------------------


async def test_the_nightly_audit_repairs_drift_once_and_then_finds_nothing(db_session):
    from app.database import AsyncSessionLocal
    from app.services import policy_audit

    host = await _host(db_session)
    sea = await _property(db_session, host, "Sea View")
    sea_id = sea.id
    await policy_service.resync(db_session, host.id, None)
    # A policy appears without the usual re-sync (the kind of gap the audit exists to catch).
    db_session.add(Policy(user_id=host.id, topic="smoking", title="Smoking", text="No smoking.", status="active", review_notes=[]))
    await db_session.commit()
    # kb_retrieval would notice at read time; here we look at what is STORED.
    assert _policy_facts(await _fact_texts(db_session, sea)) == []

    stats = await policy_audit.audit_and_repair_all(AsyncSessionLocal)

    assert stats["failed_hosts"] == 0 and stats["repaired"] >= 1
    db_session.expire_all()
    assert any("No smoking" in t for t in _policy_facts(await _fact_texts(db_session, sea_id)))
    again = await policy_audit.audit_and_repair_all(AsyncSessionLocal)
    assert again["repaired"] == 0
