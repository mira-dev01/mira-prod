from app.models.call_session import CallSession
from app.models.guest_profile import GuestProfile


async def test_property_call_appears_in_calls_list(client, auth_headers, test_call_session):
    resp = await client.get("/api/v1/calls", headers=auth_headers)
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert str(test_call_session.id) in ids


async def test_lead_agent_call_with_no_property_appears_in_calls_list(client, auth_headers, test_user, db_session):
    """Regression test: Lead Agent calls have property_id=NULL by design (no
    single property), which used to make them invisible -- `property_id IN
    (...)` never matches NULL. Calls must be scoped by user_id instead."""
    session = CallSession(
        exotel_call_id="lead-agent-call-1",
        user_id=test_user.id,
        property_id=None,
        caller_number="+919999999999",
        status="completed",
        transcript="assistant: Hi! | user: Hello",
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    list_resp = await client.get("/api/v1/calls", headers=auth_headers)
    assert list_resp.status_code == 200
    ids = [c["id"] for c in list_resp.json()]
    assert str(session.id) in ids

    get_resp = await client.get(f"/api/v1/calls/{session.id}", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["transcript"] == "assistant: Hi! | user: Hello"


async def test_lead_agent_call_counts_in_analytics_summary(client, auth_headers, test_user, db_session):
    session = CallSession(
        exotel_call_id="lead-agent-call-2",
        user_id=test_user.id,
        property_id=None,
        caller_number="+919999999999",
        status="completed",
    )
    db_session.add(session)
    await db_session.commit()

    resp = await client.get("/api/v1/analytics/summary", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["total_calls"] >= 1
    assert resp.json()["completed_calls"] >= 1


async def test_lead_agent_call_guest_appears_in_guests_list(client, auth_headers, test_user, db_session):
    guest = GuestProfile(phone="+919999999999")
    db_session.add(guest)
    await db_session.commit()
    await db_session.refresh(guest)

    session = CallSession(
        exotel_call_id="lead-agent-call-3",
        user_id=test_user.id,
        property_id=None,
        guest_profile_id=guest.id,
        caller_number="+919999999999",
        status="completed",
    )
    db_session.add(session)
    await db_session.commit()

    resp = await client.get("/api/v1/guests", headers=auth_headers)
    assert resp.status_code == 200
    ids = [g["id"] for g in resp.json()]
    assert str(guest.id) in ids


async def test_call_from_other_host_not_visible(client, auth_headers, db_session):
    """A call belonging to a different host must never show up here, even
    though it's a perfectly valid row in the same table."""
    session = CallSession(
        exotel_call_id="other-hosts-call",
        user_id=None,  # unattributed -- simulates a call to a number nobody owns
        property_id=None,
        caller_number="+910000000000",
        status="completed",
    )
    db_session.add(session)
    await db_session.commit()

    resp = await client.get("/api/v1/calls", headers=auth_headers)
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert str(session.id) not in ids
