"""Covers app/services/usage_meter.py -- fail-open usage persistence."""

import asyncio
from types import SimpleNamespace

from sqlalchemy import select

from app.config import settings
from app.models.service_usage_event import ServiceUsageEvent
from app.services import usage_meter
from app.voice.call_metrics import UsageRow


async def _rows(db):
    return (await db.scalars(select(ServiceUsageEvent))).all()


async def _drain():
    while usage_meter._background_tasks:
        await asyncio.gather(*list(usage_meter._background_tasks))


async def test_record_call_usage_persists_nonzero_rows(db_session, test_call_session, test_user):
    await usage_meter.record_call_usage(
        db_session,
        test_call_session.id,
        test_user.id,
        [UsageRow("groq", "prompt_tokens", 1200, "openai/gpt-oss-120b"), UsageRow("sarvam_tts", "characters", 0)],
    )
    rows = await _rows(db_session)
    assert len(rows) == 1
    assert rows[0].call_session_id == test_call_session.id
    assert rows[0].quantity == 1200


async def test_llm_response_usage_openai_and_anthropic_shapes(db_session):
    usage_meter.record_llm_response_usage(
        "groq", SimpleNamespace(usage=SimpleNamespace(prompt_tokens=50, completion_tokens=10)), model="m1", purpose="call_summary"
    )
    usage_meter.record_llm_response_usage(
        "anthropic", SimpleNamespace(usage=SimpleNamespace(input_tokens=30, output_tokens=7)), model="m2", purpose="x"
    )
    await _drain()
    rows = {(r.service, r.unit): r for r in await _rows(db_session)}
    assert rows[("groq", "prompt_tokens")].quantity == 50
    assert rows[("anthropic", "completion_tokens")].quantity == 7
    assert rows[("groq", "prompt_tokens")].metadata_json == {"purpose": "call_summary"}


async def test_kill_switch_disables_metering(db_session, monkeypatch):
    monkeypatch.setattr(settings, "usage_metering_enabled", False)
    usage_meter.record_usage_detached("resend", "emails", 1)
    await _drain()
    assert await _rows(db_session) == []


async def test_malformed_response_never_raises(db_session):
    usage_meter.record_llm_response_usage("groq", object(), purpose="x")
    usage_meter.record_llm_response_usage("groq", {"usage": "garbage"}, purpose="x")
    await _drain()


async def test_bound_call_context_attributes_detached_usage(db_session, test_call_session, test_user):
    """bind_usage_context at pipeline start must reach usage recorded later
    from tasks spawned inside the call (tool handlers, post-call LLM) -- and
    must not leak into an unrelated task started from a fresh context."""
    async def _inside_call():
        usage_meter.bind_usage_context(test_call_session.id, test_user.id)

        async def _tool_handler():
            usage_meter.record_usage_detached("twilio_whatsapp", "messages", 1)

        await asyncio.create_task(_tool_handler())

    await asyncio.create_task(_inside_call())
    # The binding lived in the call's own task context -- this (outer)
    # context, like any other concurrent request, never sees it.
    usage_meter.record_usage_detached("resend", "emails", 1)
    await _drain()

    rows = {r.service: r for r in await _rows(db_session)}
    assert rows["twilio_whatsapp"].call_session_id == test_call_session.id
    assert rows["twilio_whatsapp"].user_id == test_user.id
    assert rows["resend"].call_session_id is None
