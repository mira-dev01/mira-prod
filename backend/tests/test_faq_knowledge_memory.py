"""Covers Knowledge Memory's semantic FAQ-gap dedup and auto-draft
suggestions (memory-architecture-plan.md sections 3.1/3.2). Uses REAL
embeddings (OpenRouter, same model validated empirically in
embedding_service.py) against real paraphrase and unrelated question
pairs -- not mocked or synthetic, per the plan's own verification
requirement."""

import uuid

from app.models.faq_entry import FaqEntry
from app.models.unanswered_question import UnansweredQuestion
from app.services import embedding_service, faq_service


async def test_semantically_similar_gaps_are_merged(db_session, test_user):
    """'is there parking' and 'where can I park' are different
    normalized_question strings but genuinely the same question --
    real embeddings must merge them into one displayed gap with a summed
    count, which exact-text grouping alone cannot do."""
    embedding_a = await embedding_service.get_embedding("is there parking")
    embedding_b = await embedding_service.get_embedding("where can I park")
    assert embedding_a is not None and embedding_b is not None  # real API call succeeded

    db_session.add_all(
        [
            UnansweredQuestion(
                user_id=test_user.id,
                question="is there parking",
                normalized_question="is there parking",
                question_embedding=embedding_a,
            ),
            UnansweredQuestion(
                user_id=test_user.id,
                question="where can I park",
                normalized_question="where can i park",
                question_embedding=embedding_b,
            ),
        ]
    )
    await db_session.commit()

    gaps = await faq_service.list_faq_gaps(db_session, test_user.id)
    assert len(gaps) == 1
    assert gaps[0].count == 2


async def test_unrelated_gaps_are_not_merged(db_session, test_user):
    embedding_a = await embedding_service.get_embedding("is there parking")
    embedding_b = await embedding_service.get_embedding("is breakfast included")
    assert embedding_a is not None and embedding_b is not None

    db_session.add_all(
        [
            UnansweredQuestion(
                user_id=test_user.id,
                question="is there parking",
                normalized_question="is there parking",
                question_embedding=embedding_a,
            ),
            UnansweredQuestion(
                user_id=test_user.id,
                question="is breakfast included",
                normalized_question="is breakfast included",
                question_embedding=embedding_b,
            ),
        ]
    )
    await db_session.commit()

    gaps = await faq_service.list_faq_gaps(db_session, test_user.id)
    assert len(gaps) == 2


async def test_gap_gets_suggested_answer_from_verified_entry(db_session, test_user):
    """The core section 3.2 scenario: a host already verified an answer to
    'where can I park', then a guest asks the paraphrase 'is there
    parking' -- the gap must surface that existing answer as a
    suggestion."""
    verified_embedding = await embedding_service.get_embedding("where can I park")
    gap_embedding = await embedding_service.get_embedding("is there parking available")
    assert verified_embedding is not None and gap_embedding is not None

    entry = FaqEntry(
        user_id=test_user.id,
        question="where can I park",
        answer="Free parking is available in the driveway.",
        status="verified",
        question_embedding=verified_embedding,
    )
    db_session.add(entry)
    db_session.add(
        UnansweredQuestion(
            user_id=test_user.id,
            question="is there parking available",
            normalized_question="is there parking available",
            question_embedding=gap_embedding,
        )
    )
    await db_session.commit()

    gaps = await faq_service.list_faq_gaps(db_session, test_user.id)
    assert len(gaps) == 1
    assert gaps[0].suggested_answer == "Free parking is available in the driveway."
    assert gaps[0].suggested_faq_entry_id == entry.id
    assert gaps[0].match_score >= embedding_service.SEMANTIC_MATCH_THRESHOLD


async def test_unrelated_verified_entry_does_not_suggest(db_session, test_user):
    verified_embedding = await embedding_service.get_embedding("is breakfast included")
    gap_embedding = await embedding_service.get_embedding("is there parking")
    assert verified_embedding is not None and gap_embedding is not None

    db_session.add(
        FaqEntry(
            user_id=test_user.id,
            question="is breakfast included",
            answer="No, breakfast is not included.",
            status="verified",
            question_embedding=verified_embedding,
        )
    )
    db_session.add(
        UnansweredQuestion(
            user_id=test_user.id,
            question="is there parking",
            normalized_question="is there parking",
            question_embedding=gap_embedding,
        )
    )
    await db_session.commit()

    gaps = await faq_service.list_faq_gaps(db_session, test_user.id)
    assert len(gaps) == 1
    assert gaps[0].suggested_answer is None


async def test_pending_faq_entry_never_suggested(db_session, test_user):
    """A FaqEntry with status='pending' (not yet verified) must never be
    suggested -- only status='verified' entries are eligible, same gate
    search_faq_entries itself enforces. Real behavioral test: a
    near-identical embedding on a pending entry must not surface as a
    suggestion."""
    verified_embedding = await embedding_service.get_embedding("where can I park")
    gap_embedding = await embedding_service.get_embedding("is there parking")
    assert verified_embedding is not None and gap_embedding is not None

    db_session.add(
        FaqEntry(
            user_id=test_user.id,
            question="where can I park",
            answer="Free parking available.",
            status="pending",  # not yet verified by the host
            question_embedding=verified_embedding,
        )
    )
    db_session.add(
        UnansweredQuestion(
            user_id=test_user.id,
            question="is there parking",
            normalized_question="is there parking",
            question_embedding=gap_embedding,
        )
    )
    await db_session.commit()

    gaps = await faq_service.list_faq_gaps(db_session, test_user.id)
    assert len(gaps) == 1
    assert gaps[0].suggested_answer is None


async def test_gap_with_no_embedding_gets_no_suggestion_and_is_not_merged(db_session, test_user):
    """Rows where the fire-and-forget embedding backfill hasn't completed
    yet (or failed) must not error and must not be treated as a match for
    anything -- absence of an embedding is a safe no-op, not a crash."""
    db_session.add(
        UnansweredQuestion(
            user_id=test_user.id,
            question="is there parking",
            normalized_question="is there parking",
            question_embedding=None,
        )
    )
    db_session.add(
        FaqEntry(
            user_id=test_user.id,
            question="where can I park",
            answer="Free parking available.",
            status="verified",
            question_embedding=None,
        )
    )
    await db_session.commit()

    gaps = await faq_service.list_faq_gaps(db_session, test_user.id)
    assert len(gaps) == 1
    assert gaps[0].suggested_answer is None


async def test_answer_faq_gap_reuses_gap_embedding_without_new_api_call(db_session, test_user, monkeypatch):
    """answer_faq_gap should copy the gap's own embedding onto the new
    FaqEntry instead of triggering a fresh embedding API call when one is
    already available -- confirmed by making a fresh call raise and
    checking the entry still ends up with the embedding."""
    embedding = await embedding_service.get_embedding("where can I park")
    assert embedding is not None

    gap = UnansweredQuestion(
        user_id=test_user.id,
        question="where can I park",
        normalized_question="where can i park",
        question_embedding=embedding,
    )
    db_session.add(gap)
    await db_session.commit()
    await db_session.refresh(gap)

    async def _boom(*args, **kwargs):
        raise AssertionError("should not call get_embedding when gap already has one")

    monkeypatch.setattr(embedding_service, "get_embedding", _boom)

    entry = await faq_service.answer_faq_gap(db_session, gap, "Free parking available.", False, "host")
    assert entry.question_embedding == embedding
