"""users.agent_customized_at: stamped when the host changes something the agent says or does,
never by unrelated rows, never onto another host; GET /agent-config/test-status compares it with
the host's own browser test calls."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.database import Base
from app.models.call_session import CallSession
from app.models.faq_entry import FaqEntry
from app.models.lead import Lead
from app.models.negotiation_rule import NegotiationRule
from app.models.pricing_rule import PricingRule
from app.models.property import Property
from app.models.user import User
from app.services import agent_change_tracker
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER
from tests.conftest import auth_headers_for


async def _stamp(db_session, user) -> datetime | None:
    await db_session.refresh(user)
    return user.agent_customized_at


async def _other_host(db_session) -> User:
    other = User(email=f"o-{uuid.uuid4().hex[:8]}@example.com", clerk_user_id=f"user_{uuid.uuid4().hex[:16]}")
    db_session.add(other)
    await db_session.commit()
    return other


async def test_new_user_has_no_change(db_session, test_user):
    assert await _stamp(db_session, test_user) is None


async def test_faq_insert_update_and_delete_stamp_only_the_owner(db_session, test_user, test_property):
    other = await _other_host(db_session)
    entry = FaqEntry(user_id=test_user.id, property_id=test_property.id, question="Pets?", answer="Yes")
    db_session.add(entry)
    await db_session.commit()
    first = await _stamp(db_session, test_user)
    assert first is not None
    assert await _stamp(db_session, other) is None

    entry.answer = "No"
    await db_session.commit()
    assert await _stamp(db_session, test_user) >= first

    test_user.agent_customized_at = None
    await db_session.commit()
    await db_session.delete(entry)
    await db_session.commit()
    assert await _stamp(db_session, test_user) is not None


async def test_pricing_rule_resolves_owner_through_property(db_session, test_user, test_property):
    rule_columns = {c.name for c in PricingRule.__table__.columns if not c.nullable and c.default is None and c.server_default is None}
    # Build the smallest valid rule from whatever the model requires beyond its id/timestamps.
    assert "property_id" in rule_columns
    rule = PricingRule(property_id=test_property.id, **{
        name: _filler(PricingRule.__table__.columns[name])
        for name in rule_columns - {"id", "property_id", "created_at", "updated_at"}
    })
    db_session.add(rule)
    await db_session.commit()
    assert await _stamp(db_session, test_user) is not None


def _filler(column):
    python_type = column.type.python_type
    if python_type is str:
        return "x"
    if python_type in (int, float):
        return 1
    if python_type is bool:
        return True
    if python_type is datetime:
        return datetime.now(timezone.utc)
    import datetime as _dt, decimal
    if python_type is _dt.date:
        return _dt.date.today()
    if python_type is decimal.Decimal:
        return decimal.Decimal(1)
    return None


async def test_agent_settings_on_user_row_stamp_but_other_profile_fields_do_not(db_session, test_user):
    test_user.name = "New Name"
    await db_session.commit()
    assert await _stamp(db_session, test_user) is None

    test_user.agent_persona = "Warm and brief"
    await db_session.commit()
    assert await _stamp(db_session, test_user) is not None


async def test_property_agent_fields_stamp_but_sync_state_does_not(db_session, test_user, test_property):
    test_user.agent_customized_at = None
    await db_session.commit()

    test_property.booking_counter = (test_property.booking_counter or 0) + 1
    await db_session.commit()
    assert await _stamp(db_session, test_user) is None

    test_property.house_rules = "Quiet after 10pm."
    await db_session.commit()
    assert await _stamp(db_session, test_user) is not None


async def test_call_logs_and_leads_never_stamp(db_session, test_user, test_property):
    test_user.agent_customized_at = None
    await db_session.commit()
    db_session.add(CallSession(user_id=test_user.id, property_id=test_property.id, caller_number="+919999999999", status="completed"))
    db_session.add(Lead(user_id=test_user.id, guest_name="A", status="open"))
    await db_session.commit()
    assert await _stamp(db_session, test_user) is None


async def test_every_owned_model_is_tracked_or_explicitly_ignored():
    """A new table with an owner must be a deliberate choice: tracked here, or listed in IGNORED_MODELS."""
    tracked = {m.__name__ for m in agent_change_tracker.TRACKED_MODELS}
    undecided = []
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        owned = {"user_id", "host_id", "property_id"} & {c.key for c in mapper.columns}
        if owned and cls.__name__ not in tracked and cls.__name__ not in agent_change_tracker.IGNORED_MODELS:
            undecided.append(cls.__name__)
    assert undecided == [], f"decide whether these change the agent, then add to TRACKED_MODELS or IGNORED_MODELS: {undecided}"


async def test_test_status_flow(client, db_session, test_user, test_property, auth_headers):
    status = (await client.get("/api/v1/agent-config/test-status", headers=auth_headers)).json()
    assert status["needs_test"] is False and status["changed_at"] is None

    test_property.house_rules = "No parties."
    await db_session.commit()
    status = (await client.get("/api/v1/agent-config/test-status", headers=auth_headers)).json()
    assert status["needs_test"] is True and status["changed_at"] is not None

    # Another host's browser test does not clear this host's highlight.
    other = await _other_host(db_session)
    db_session.add(CallSession(user_id=other.id, caller_number=BROWSER_TEST_CALLER_NUMBER, status="completed", started_at=datetime.now(timezone.utc) + timedelta(seconds=5)))
    await db_session.commit()
    assert (await client.get("/api/v1/agent-config/test-status", headers=auth_headers)).json()["needs_test"] is True

    # A real (non-test) call does not either.
    db_session.add(CallSession(user_id=test_user.id, caller_number="+919999999999", status="completed", started_at=datetime.now(timezone.utc) + timedelta(seconds=10)))
    await db_session.commit()
    assert (await client.get("/api/v1/agent-config/test-status", headers=auth_headers)).json()["needs_test"] is True

    # The host's own browser test clears it.
    db_session.add(CallSession(user_id=test_user.id, caller_number=BROWSER_TEST_CALLER_NUMBER, status="completed", started_at=datetime.now(timezone.utc) + timedelta(seconds=20)))
    await db_session.commit()
    assert (await client.get("/api/v1/agent-config/test-status", headers=auth_headers)).json()["needs_test"] is False

    # Changing something again re-arms it.
    test_property.house_rules = "No pets."
    await db_session.commit()
    assert (await client.get("/api/v1/agent-config/test-status", headers=auth_headers)).json()["needs_test"] is True
