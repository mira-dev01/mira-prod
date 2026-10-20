import uuid

from sqlalchemy import select

from app.models.call_quality_event import CallQualityEvent
from app.services import call_service
from app.voice.conversation_quality import ConversationQuality, ValidationResult


async def test_record_quality_events_persists_all_validations(db_session, test_call_session):
    quality = ConversationQuality()
    quality.record(
        ValidationResult(
            rule="style_compliance",
            severity="FAIL",
            confidence=0.9,
            turn_index=3,
            processing_time_ms=12.5,
            metadata={"lang": "hi"},
        )
    )
    quality.record(
        ValidationResult(
            rule="response_shape",
            severity="WARNING",
            confidence=0.5,
            turn_index=5,
            processing_time_ms=3.1,
        )
    )

    await call_service.record_quality_events(db_session, test_call_session.id, quality)

    rows = (
        (
            await db_session.execute(
                select(CallQualityEvent).where(CallQualityEvent.call_session_id == test_call_session.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    by_rule = {row.rule: row for row in rows}
    assert by_rule["style_compliance"].severity == "FAIL"
    assert by_rule["style_compliance"].metadata_json == {"lang": "hi"}
    assert by_rule["response_shape"].metadata_json == {}


async def test_record_quality_events_empty_is_noop(db_session, test_call_session):
    await call_service.record_quality_events(db_session, test_call_session.id, ConversationQuality())

    rows = (
        (
            await db_session.execute(
                select(CallQualityEvent).where(CallQualityEvent.call_session_id == test_call_session.id)
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


async def test_record_quality_events_none_call_session_id_is_noop(db_session):
    quality = ConversationQuality()
    quality.record(ValidationResult(rule="style_compliance", severity="FAIL", confidence=0.9, turn_index=1))

    # Must not raise -- mirrors set_call_classification/set_call_summary's
    # own None-tolerant shape.
    await call_service.record_quality_events(db_session, None, quality)


async def test_record_quality_events_unknown_call_session_id_fails_open(db_session):
    quality = ConversationQuality()
    quality.record(ValidationResult(rule="style_compliance", severity="FAIL", confidence=0.9, turn_index=1))

    # A nonexistent call_session_id must not raise into the caller (the
    # existence pre-check returns early), and the session must stay usable
    # afterward for whatever on_pipeline_finished does next.
    await call_service.record_quality_events(db_session, uuid.uuid4(), quality)
    await db_session.execute(select(1))
