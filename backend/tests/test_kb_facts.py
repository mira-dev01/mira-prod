"""Knowledge-base fact building and syncing (app/services/property/chunking.py)."""

import uuid

import pytest
from sqlalchemy import select

from app.models.faq_entry import FaqEntry
from app.models.property import Property
from app.models.property_chunk import PropertyChunk
from app.services.property.chunking import (
    FACT_CHUNK_TYPE,
    build_fact_chunks,
    sync_property_chunks,
    sync_property_facts,
)


@pytest.fixture(autouse=True)
def _no_network_embeddings(monkeypatch):
    from app.services import embedding_service

    async def _none(text):
        return None

    monkeypatch.setattr(embedding_service, "get_embedding", _none)


def _property(**overrides) -> Property:
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
        amenities=[],
        landmarks=[],
        seasonal_notes=[],
        house_rules=None,
        neighborhood_info=None,
        usp=None,
    )
    fields.update(overrides)
    return Property(**fields)


def test_structured_facts_always_present():
    texts = [d.text for d in build_fact_chunks(_property(), [])]
    assert any("Check-in time is 14:00" in t and "Check-out time is 11:00" in t for t in texts)
    assert any("maximum of 6 guests" in t for t in texts)


def test_minimum_nights_fact_only_when_restrictive():
    assert not any("minimum stay" in d.text for d in build_fact_chunks(_property(minimum_nights=1), []))
    assert any("minimum stay is 2 nights" in d.text for d in build_fact_chunks(_property(minimum_nights=2), []))


def test_neighborhood_is_split_into_sentence_facts_not_one_blob():
    info = " ".join(f"Place number {i} is a {i}0 minute drive away from the villa and worth visiting." for i in range(1, 9))
    drafts = [d for d in build_fact_chunks(_property(neighborhood_info=info), []) if d.source_kind == "neighborhood"]
    assert len(drafts) > 1
    assert all(len(d.text) < 400 for d in drafts)
    assert all(d.text.startswith("Neighborhood: ") for d in drafts)


def test_house_rules_split_by_line():
    drafts = [d for d in build_fact_chunks(_property(house_rules="No smoking indoors.\nQuiet hours after 10 PM."), [])
              if d.source_kind == "house_rules"]
    assert any("No smoking indoors" in d.text for d in drafts)
    assert any("Quiet hours after 10 PM" in d.text for d in drafts)


def test_landmarks_render_with_distance_and_mode():
    prop = _property(landmarks=[{"name": "Mall Road", "distance_minutes": 12, "mode": "drive"}, {"name": "Old Temple"}])
    texts = [d.text for d in build_fact_chunks(prop, []) if d.source_kind == "neighborhood"]
    assert "Neighborhood: Mall Road is 12 minutes away by drive." in texts
    assert "Neighborhood: near Old Temple." in texts


def test_amenities_grouped():
    amenities = [f"Amenity {i}" for i in range(20)]
    drafts = [d for d in build_fact_chunks(_property(amenities=amenities), []) if d.source_kind == "amenity"]
    assert len(drafts) == 3
    assert "Amenity 0" in drafts[0].text and "Amenity 19" in drafts[-1].text


def test_seasonal_note_keeps_its_month_window():
    prop = _property(seasonal_notes=[{"note": "Pool closed in monsoon.", "start_month": 6, "end_month": 8}])
    drafts = [d for d in build_fact_chunks(prop, []) if d.source_kind == "seasonal_note"]
    assert drafts[0].meta == {"start_month": 6, "end_month": 8}


def test_malformed_seasonal_note_skipped():
    prop = _property(seasonal_notes=[{"note": "no months"}, {"start_month": 1, "end_month": 2}])
    assert [d for d in build_fact_chunks(prop, []) if d.source_kind == "seasonal_note"] == []


def test_verified_faq_and_legacy_faq_become_facts():
    entry = FaqEntry(id=uuid.uuid4(), question="Is there parking?", answer="Yes, two cars.", status="verified")
    prop = _property(faq=[{"question": "Is wifi free?", "answer": "Yes, 100 Mbps."}])
    texts = [d.text for d in build_fact_chunks(prop, [entry])]
    assert "Q: Is there parking? A: Yes, two cars." in texts
    assert "Q: Is wifi free? A: Yes, 100 Mbps." in texts


def test_long_faq_answer_is_split_but_keeps_its_question():
    answer = " ".join(f"Sentence number {i} says something specific about the area." for i in range(30))
    entry = FaqEntry(id=uuid.uuid4(), question="Tell me about the area", answer=answer, status="verified")
    drafts = [d for d in build_fact_chunks(_property(), [entry]) if d.source_kind == "faq_entry"]
    assert len(drafts) > 1
    assert all(d.text.startswith("Q: Tell me about the area A: ") for d in drafts)


def test_duplicate_text_is_deduplicated():
    entry = FaqEntry(id=uuid.uuid4(), question="Q?", answer="Same.", status="verified")
    twin = FaqEntry(id=uuid.uuid4(), question="Q?", answer="Same.", status="verified")
    texts = [d.text for d in build_fact_chunks(_property(), [entry, twin])]
    assert texts.count("Q: Q? A: Same.") == 1


def test_description_is_a_fact_not_cut_mid_sentence():
    usp = "Glass house with a private jacuzzi. Sleeps four. Not suitable for children under five."
    drafts = [d for d in build_fact_chunks(_property(usp=usp), []) if d.source_kind == "description"]
    assert "Not suitable for children under five." in " ".join(d.text for d in drafts)


async def test_sync_writes_facts_and_only_verified_faq(db_session, test_property, test_user):
    db_session.add_all(
        [
            FaqEntry(user_id=test_user.id, property_id=test_property.id, question="Pets?", answer="Yes, small dogs.", status="verified"),
            FaqEntry(user_id=test_user.id, property_id=test_property.id, question="Secret?", answer="Draft only.", status="pending"),
        ]
    )
    await db_session.commit()

    count = await sync_property_facts(db_session, test_property)

    rows = (
        await db_session.scalars(
            select(PropertyChunk).where(
                PropertyChunk.property_id == test_property.id, PropertyChunk.chunk_type == FACT_CHUNK_TYPE
            )
        )
    ).all()
    assert count == len(rows) > 0
    texts = [r.text for r in rows]
    assert any("small dogs" in t for t in texts)
    assert not any("Draft only" in t for t in texts)
    assert {r.source_kind for r in rows} >= {"structured", "faq_entry"}


async def test_sync_is_idempotent_and_replaces(db_session, test_property):
    first = await sync_property_facts(db_session, test_property)
    second = await sync_property_facts(db_session, test_property)
    assert first == second
    total = len(
        (await db_session.scalars(select(PropertyChunk).where(PropertyChunk.property_id == test_property.id, PropertyChunk.chunk_type == FACT_CHUNK_TYPE))).all()
    )
    assert total == second


async def test_sync_reuses_embeddings_for_unchanged_text(db_session, test_property):
    await sync_property_facts(db_session, test_property)
    rows = (await db_session.scalars(select(PropertyChunk).where(PropertyChunk.property_id == test_property.id, PropertyChunk.chunk_type == FACT_CHUNK_TYPE))).all()
    rows[0].embedding = [0.1, 0.2, 0.3]
    kept_text = rows[0].text
    await db_session.commit()

    await sync_property_facts(db_session, test_property)

    again = (await db_session.scalars(select(PropertyChunk).where(PropertyChunk.property_id == test_property.id, PropertyChunk.chunk_type == FACT_CHUNK_TYPE, PropertyChunk.text == kept_text))).one()
    assert again.embedding == [0.1, 0.2, 0.3]


async def test_coarse_chunk_sync_does_not_delete_facts_and_vice_versa(db_session, test_property):
    await sync_property_facts(db_session, test_property)
    await sync_property_chunks(db_session, test_property)

    types = {r.chunk_type for r in (await db_session.scalars(select(PropertyChunk).where(PropertyChunk.property_id == test_property.id))).all()}
    assert FACT_CHUNK_TYPE in types
    assert "overview" in types or "amenities" in types

    await sync_property_facts(db_session, test_property)
    types_after = {r.chunk_type for r in (await db_session.scalars(select(PropertyChunk).where(PropertyChunk.property_id == test_property.id))).all()}
    assert types_after == types
