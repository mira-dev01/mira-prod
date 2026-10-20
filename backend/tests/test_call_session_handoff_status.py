"""Call Ownership Schedule / Host Take Call, Phase 1: handoff_status is a
pure storage column in this phase -- nothing writes a non-NULL value yet.
These tests only confirm the column exists, defaults/stays NULL, and does
not disturb any other CallSession behavior."""

import uuid

from app.models.call_session import CallSession


async def test_new_call_session_defaults_handoff_status_to_null(test_call_session):
    assert test_call_session.handoff_status is None


async def test_call_session_can_be_created_without_handoff_status(db_session, test_property, test_user):
    session = CallSession(
        exotel_call_id=f"call-{uuid.uuid4().hex[:8]}",
        user_id=test_user.id,
        property_id=test_property.id,
        caller_number="+919999999999",
        status="in_progress",
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    assert session.handoff_status is None


async def test_call_session_handoff_status_round_trips_when_explicitly_set(db_session, test_property, test_user):
    """Storage-level round-trip only -- no application code sets this value
    yet in this phase. Uses the future value names directly to confirm the
    column can hold them without any DB-level constraint (e.g. an enum
    type) rejecting them."""
    session = CallSession(
        exotel_call_id=f"call-{uuid.uuid4().hex[:8]}",
        user_id=test_user.id,
        property_id=test_property.id,
        caller_number="+919999999999",
        status="in_progress",
        handoff_status="requested",
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    assert session.handoff_status == "requested"


async def test_call_session_api_list_unaffected_by_new_column(client, auth_headers, test_call_session):
    """Regression check: the existing calls list endpoint must keep working
    unchanged -- it does not need to expose handoff_status in this phase,
    but it must not break because the underlying model gained a column."""
    resp = await client.get("/api/v1/calls", headers=auth_headers)
    assert resp.status_code == 200
    ids = [row["id"] for row in resp.json()]
    assert str(test_call_session.id) in ids
