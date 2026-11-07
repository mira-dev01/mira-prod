"""Party/event rules per property: what the host's approved policies say about
each property (labels), how recommend_properties uses that for a guest who wants
to host a gathering, and the audit that every property carries the right
knowledge. Real Postgres; embeddings stubbed so nothing touches the network."""

import uuid

import pytest
from sqlalchemy import delete, select

from app.models.faq_entry import FaqEntry
from app.models.policy import Policy
from app.models.property import Property
from app.models.property_chunk import PropertyChunk
from app.models.user import User
from app.prompts.system_prompt import _gathering_section
from app.schemas.tool import RecommendPropertiesArgs
from app.services import embedding_service, policy_audit, policy_service, policy_stance
from app.services.policy_service import UNMATCHED_NOTE_MARK
from app.services.property import chunking, kb_retrieval
from app.services.property.pitch_formatter import render_recommendation_text
from app.services.property.retrieval import orchestrator


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(embedding_service, "backfill_property_chunk_embeddings", _noop)
    monkeypatch.setattr(embedding_service, "backfill_property_chunk_embedding", _noop)
    monkeypatch.setattr(embedding_service, "get_embedding", _noop)
    kb_retrieval.invalidate_fact_cache()
    yield
    kb_retrieval.invalidate_fact_cache()


async def _host(db) -> User:
    user = User(email=f"h-{uuid.uuid4().hex[:8]}@example.com", clerk_user_id=f"user_{uuid.uuid4().hex[:16]}", name="Host")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _prop(db, host, name, price=4000, guests=10, **kw) -> Property:
    prop = Property(
        user_id=host.id, name=name, city="Goa", base_price=price, max_guests=guests,
        exophone=f"+9180{uuid.uuid4().int % 10**8:08d}", **kw,
    )
    db.add(prop)
    await db.commit()
    await db.refresh(prop)
    await chunking.sync_property_chunks(db, prop)  # as an import or an edit would
    return prop


# ── reading the host's words ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,kind,cap",
    [
        ("Parties are not allowed.", "not_allowed", None),
        ("Parties aren't allowed at the villa.", "not_allowed", None),
        ("No parties or events.", "not_allowed", None),
        ("Parties are welcome.", "allowed", None),
        ("Events are allowed.", "allowed", None),
        ("Small gatherings only are allowed.", "limited", None),
        ("Parties allowed with prior approval.", "limited", None),
        ("Gatherings up to 20 guests are fine.", "limited", 20),
        ("Parties are not allowed, but small family gatherings are.", "limited", None),
        ("Pets are welcome.", "allowed", None),
        ("Every guest must show a photo ID.", None, None),
    ],
)
def test_the_stance_is_read_from_the_hosts_words(text, kind, cap):
    assert policy_stance.classify(text) == (kind, cap)


async def _party(db, host, text, property_id=None, notes=None) -> Policy:
    policy = Policy(
        user_id=host.id, property_id=property_id, topic="parties_events", title="Parties & events",
        text=text, status="active", review_notes=notes or [],
    )
    db.add(policy)
    await db.commit()
    await policy_service.resync(db, host.id, None)
    return policy


async def _stances(db, host):
    props = list((await db.scalars(select(Property).where(Property.user_id == host.id))).all())
    active = list((await db.scalars(select(Policy).where(Policy.user_id == host.id, Policy.status == "active"))).all())
    stances = policy_stance.stances_for_host(active, props, policy_stance.PARTIES)
    return {p.name: stances[p.id] for p in props}, active, props


async def test_a_hostwide_rule_that_names_one_property_means_only_that_one(db_session):
    host = await _host(db_session)
    await _prop(db_session, host, "Casa Blu | 4BHK villa")
    await _prop(db_session, host, "Sea View")
    await _party(db_session, host, "Parties only at Casa Blu.")

    stances, *_ = await _stances(db_session, host)

    assert stances["Casa Blu | 4BHK villa"].kind == "allowed"
    assert stances["Sea View"].kind == "not_allowed"


async def test_a_rule_naming_a_property_does_not_decide_for_the_others(db_session):
    host = await _host(db_session)
    await _prop(db_session, host, "Bombay Breeze")
    await _prop(db_session, host, "Sea View")
    await _party(db_session, host, "Parties are not allowed at Bombay Breeze.")

    stances, *_ = await _stances(db_session, host)

    assert stances["Bombay Breeze"].kind == "not_allowed"
    assert stances["Sea View"] is None  # nothing on file, so nothing is filtered or claimed


async def test_a_property_override_beats_the_hostwide_rule(db_session):
    host = await _host(db_session)
    a, b = await _prop(db_session, host, "Casa Blu"), await _prop(db_session, host, "Sea View")
    await _party(db_session, host, "Parties are not allowed.")
    await _party(db_session, host, "Parties are welcome.", property_id=a.id)

    stances, *_ = await _stances(db_session, host)

    assert (stances["Casa Blu"].kind, stances["Sea View"].kind) == ("allowed", "not_allowed")


async def test_a_policy_about_a_place_that_is_not_the_hosts_is_never_read_as_a_rule(db_session):
    host = await _host(db_session)
    await _prop(db_session, host, "Sea View")
    await _party(db_session, host, "Parties are welcome at Crystal Hall.", notes=[f"The document mentions Crystal Hall, which {UNMATCHED_NOTE_MARK}."])

    stances, *_ = await _stances(db_session, host)

    assert stances["Sea View"] is None


async def test_a_hostwide_policy_that_names_a_property_is_only_about_that_property(db_session):
    host = await _host(db_session)
    deccan = await _prop(db_session, host, "Deccan Escape 4 BHK Luxury Stay")
    sea = await _prop(db_session, host, "Sea View")
    # Approved back when "Deccan Escape" was not yet one of the host's properties: the review note is stale.
    await policy_service.resync(db_session, host.id, None)
    db_session.add(
        Policy(user_id=host.id, topic="pets", title="Pets", text="Pets are not allowed at Deccan Escape.", status="active",
               review_notes=[f"The document mentions Deccan Escape, which {UNMATCHED_NOTE_MARK}."])
    )
    await db_session.commit()
    await policy_service.resync(db_session, host.id, None)

    overview = await policy_service.overview(db_session, host)

    labels = {p["name"]: [x["label"] for x in p["labels"]] for p in overview["properties"]}
    assert labels["Deccan Escape 4 BHK Luxury Stay"] == ["No pets"] and labels["Sea View"] == []
    follows = {p["name"]: p["policy_ids"] for p in overview["properties"]}
    assert len(follows["Deccan Escape 4 BHK Luxury Stay"]) == 1 and follows["Sea View"] == []
    sea_facts = list((await db_session.scalars(select(PropertyChunk.text).where(PropertyChunk.property_id == sea.id))).all())
    assert not any("Deccan Escape" in f for f in sea_facts)  # Sea View is not told another property's rule
    deccan_facts = list((await db_session.scalars(select(PropertyChunk.text).where(PropertyChunk.property_id == deccan.id))).all())
    assert any("Pets are not allowed at Deccan Escape" in f for f in deccan_facts)


async def test_an_only_rule_stays_on_every_property_because_it_is_about_all_of_them(db_session):
    host = await _host(db_session)
    await _prop(db_session, host, "Casa Blu")
    sea = await _prop(db_session, host, "Sea View")
    await _party(db_session, host, "Parties only at Casa Blu.")

    facts = list((await db_session.scalars(select(PropertyChunk.text).where(PropertyChunk.property_id == sea.id))).all())

    assert any("Parties only at Casa Blu." in f for f in facts)  # so "can I party here?" gets a real answer


async def test_the_overview_labels_each_property(db_session):
    host = await _host(db_session)
    await _prop(db_session, host, "Casa Blu")
    await _prop(db_session, host, "Sea View")
    await _party(db_session, host, "Parties only at Casa Blu.")
    await policy_service.create_policy(db_session, host, topic="pets", text="Pets are not allowed.")

    overview = await policy_service.overview(db_session, host)

    labels = {p["name"]: [x["label"] for x in p["labels"]] for p in overview["properties"]}
    assert labels["Casa Blu"] == ["Parties welcome", "No pets"]
    assert labels["Sea View"] == ["No parties", "No pets"]


# ── recommending for a party ──────────────────────────────────────────────


async def _portfolio(db):
    host = await _host(db)
    casa = await _prop(db, host, "Casa Blu", price=9000, guests=20)
    sea = await _prop(db, host, "Sea View", price=3000, guests=20)  # cheapest, but no parties
    hill = await _prop(db, host, "Hill Cabin", price=5000, guests=20)  # no party rule on file
    cozy = await _prop(db, host, "Cozy Nook", price=4000, guests=20)
    await _party(db, host, "Parties are welcome at Casa Blu.", property_id=casa.id)
    await _party(db, host, "Parties are not allowed.", property_id=sea.id)
    await _party(db, host, "Small gatherings only, up to 12 guests.", property_id=cozy.id)
    return host, casa, sea, hill, cozy


def _names(result):
    return [c.spoken_name for c in result.options]


async def test_a_party_sees_only_properties_whose_policy_allows_it_best_fit_first(db_session):
    host, *_ = await _portfolio(db_session)

    result = await orchestrator.recommend_properties(
        db_session, RecommendPropertiesArgs(hosting_gathering=True, num_guests=10), host.id
    )

    # Casa Blu (host says yes) first even though it is dearest, then Cozy Nook
    # (limited to 12, fine for 10), then Hill Cabin (nothing on file). Sea View is out.
    assert _names(result) == ["Casa Blu", "Cozy Nook", "Hill Cabin"]
    text = render_recommendation_text(result)
    assert "Host policy on parties and events: Parties are welcome at Casa Blu." in text
    assert "No party or event policy is on file for this property" in text


async def test_a_party_bigger_than_a_propertys_cap_rules_it_out(db_session):
    host, *_ = await _portfolio(db_session)

    result = await orchestrator.recommend_properties(
        db_session, RecommendPropertiesArgs(hosting_gathering=True, num_guests=15), host.id
    )

    assert "Cozy Nook" not in _names(result)
    assert _names(result)[0] == "Casa Blu"


async def test_if_no_property_allows_the_party_it_says_so_instead_of_suggesting_one(db_session):
    host = await _host(db_session)
    await _prop(db_session, host, "Sea View")
    await _party(db_session, host, "Parties are not allowed.")

    result = await orchestrator.recommend_properties(db_session, RecommendPropertiesArgs(hosting_gathering=True), host.id)

    assert result.options == [] and result.gathering_blocked
    assert "allow a party or event" in render_recommendation_text(result)


async def test_an_ordinary_stay_is_not_filtered_by_party_rules(db_session):
    host, *_ = await _portfolio(db_session)

    result = await orchestrator.recommend_properties(db_session, RecommendPropertiesArgs(num_guests=4), host.id)

    assert "Sea View" in _names(result)  # cheapest, no parties -- still a fine place to stay
    assert all(c.policy_note == "" for c in result.options)


async def test_a_big_group_not_yet_called_a_party_sees_each_rule_but_keeps_every_option(db_session):
    host, *_ = await _portfolio(db_session)

    result = await orchestrator.recommend_properties(db_session, RecommendPropertiesArgs(num_guests=10), host.id)

    notes = {c.spoken_name: c.policy_note for c in result.options}
    assert "Sea View" in notes  # not removed
    assert "Parties are not allowed" in notes["Sea View"]
    assert notes.get("Hill Cabin", "") == ""  # nothing on file: no claim either way


async def test_the_prompt_only_asks_about_the_occasion_when_the_host_has_party_rules(db_session):
    host, *_ = await _portfolio(db_session)
    props = list((await db_session.scalars(select(Property).where(Property.user_id == host.id))).all())

    assert await policy_stance.host_has_party_rules(db_session, host.id, props) is True
    section = _gathering_section(True)
    assert "hosting_gathering=true" in section and "occasion" in section
    assert _gathering_section(False) == ""

    other = await _host(db_session)
    await _prop(db_session, other, "Plain House")
    plain = list((await db_session.scalars(select(Property).where(Property.user_id == other.id))).all())
    assert await policy_stance.host_has_party_rules(db_session, other.id, plain) is False


# ── every property carries the right knowledge ────────────────────────────


async def test_the_audit_is_clean_when_everything_is_synced_and_scoped_correctly(db_session):
    host, casa, sea, hill, cozy = await _portfolio(db_session)
    db_session.add(
        FaqEntry(user_id=host.id, property_id=sea.id, question="What is the cancellation policy?",
                 answer="Flexible.", category="booking", status="verified", verified_by="airbnb_import")
    )
    await db_session.commit()
    await policy_service.create_policy(db_session, host, topic="cancellation", text="Free cancellation up to 7 days before.")

    audits = {a.name: a for a in await policy_audit.audit_host(db_session, host.id)}

    assert all(a.ok for a in audits.values()), {n: a.problems for n, a in audits.items()}
    # Each property carries its own rule and the host-wide cancellation policy, nothing of another's.
    assert any("Parties are welcome at Casa Blu" in p for p in audits["Casa Blu"].policies)
    assert any("up to 12 guests" in p for p in audits["Cozy Nook"].policies)
    assert not any("up to 12 guests" in p for p in audits["Casa Blu"].policies)
    assert audits["Casa Blu"].labels == ["Parties welcome"]
    assert audits["Sea View"].labels == ["No parties"]
    assert any("imported FAQ" in n for n in audits["Sea View"].notes)  # kept, just not spoken


async def test_the_audit_catches_a_property_missing_its_policy_and_one_carrying_anothers(db_session):
    host, casa, sea, hill, cozy = await _portfolio(db_session)
    # Casa Blu loses a policy fact it should have; Sea View is handed Cozy Nook's override.
    await db_session.execute(
        delete(PropertyChunk).where(
            PropertyChunk.property_id == casa.id, PropertyChunk.chunk_type == "fact", PropertyChunk.source_kind == "policy"
        )
    )
    override = await db_session.scalar(select(Policy).where(Policy.property_id == cozy.id))
    db_session.add(
        PropertyChunk(property_id=sea.id, chunk_type="fact", text="Policy (Parties & events): Small gatherings only, up to 12 guests.",
                      source_kind="policy", source_ref=f"policy_{override.id}")
    )
    await db_session.commit()

    audits = {a.name: a for a in await policy_audit.audit_host(db_session, host.id)}

    assert any(p.startswith("stale") or p.startswith("missing") for p in audits["Casa Blu"].problems)
    assert any(p.startswith("leak") for p in audits["Sea View"].problems)
    assert audits["Hill Cabin"].ok

    fixed = await policy_audit.fix_stale(db_session, host.id, list(audits.values()))
    assert fixed == 2
    assert all(a.ok for a in await policy_audit.audit_host(db_session, host.id))


async def test_a_drafts_and_other_hosts_policies_never_reach_a_property(db_session):
    host, other = await _host(db_session), await _host(db_session)
    prop = await _prop(db_session, host, "Sea View")
    theirs = await _prop(db_session, other, "Their Place")
    db_session.add(Policy(user_id=host.id, topic="pets", title="Pets", text="Pets are welcome.", status="draft", review_notes=[]))
    await db_session.commit()
    await policy_service.create_policy(db_session, other, topic="smoking", text="No smoking anywhere.")
    await policy_service.resync(db_session, host.id, None)

    audit = (await policy_audit.audit_host(db_session, host.id))[0]
    facts = list((await db_session.scalars(select(PropertyChunk.text).where(PropertyChunk.property_id == prop.id))).all())

    assert audit.ok and not any("Pets are welcome" in f or "No smoking anywhere" in f for f in facts)
    assert any("waiting for approval" in n for n in audit.notes)
    assert theirs.id != prop.id
