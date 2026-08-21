from datetime import datetime, timedelta, timezone

from app.models.call_session import CallSession
from app.models.guest_profile import GuestProfile
from app.models.lead import Lead
from app.schemas.call_summary import BookingSnapshot, CallSummary, SummaryOutcome


async def test_property_call_appears_in_calls_list(client, auth_headers, test_call_session):
    resp = await client.get("/api/v1/calls", headers=auth_headers)
    assert resp.status_code == 200
    ids = [c["id"] for c in resp.json()]
    assert str(test_call_session.id) in ids


async def test_call_detail_returns_structured_ai_summary(client, auth_headers, test_user, db_session):
    """Full round-trip: a structured CallSummary written to the JSONB
    ai_summary column must come back from GET /calls/{id} with the exact
    nested shape the frontend (calls/[id]/page.tsx) renders -- snake_case
    keys throughout, matching frontend/src/lib/types.ts's CallSummary."""
    summary = CallSummary(
        booking_snapshot=BookingSnapshot(guest_name="Rohan", property=["Villa A", "Villa B"], guests="8"),
        conversation_summary="Guest asked about a birthday trip to Goa.",
        outcome=SummaryOutcome(status="Awaiting Guest Details", reason="Check-out date not yet confirmed."),
        host_action=["Confirm availability for 29 May."],
        key_details=["Group of 8 friends", "Birthday celebration"],
        missing_information=["Check-out date", "Budget"],
    )
    session = CallSession(
        exotel_call_id="summary-roundtrip-1",
        user_id=test_user.id,
        caller_number="+919999999999",
        status="completed",
        ai_summary=summary.model_dump(),
    )
    db_session.add(session)
    await db_session.commit()

    resp = await client.get(f"/api/v1/calls/{session.id}", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()["ai_summary"]
    assert body["booking_snapshot"]["guest_name"] == "Rohan"
    assert body["booking_snapshot"]["property"] == ["Villa A", "Villa B"]
    assert body["conversation_summary"] == "Guest asked about a birthday trip to Goa."
    assert body["outcome"]["status"] == "Awaiting Guest Details"
    assert body["host_action"] == ["Confirm availability for 29 May."]
    assert body["missing_information"] == ["Check-out date", "Budget"]


async def test_call_detail_ai_summary_is_null_when_not_yet_generated(client, auth_headers, test_call_session):
    resp = await client.get(f"/api/v1/calls/{test_call_session.id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["ai_summary"] is None


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


async def test_call_includes_duration_and_lead_name_phone(test_user, client, auth_headers, db_session):
    started = datetime.now(timezone.utc) - timedelta(minutes=4, seconds=30)
    session = CallSession(
        exotel_call_id="call-with-lead-1",
        user_id=test_user.id,
        # A real-looking caller number, not the browser-test placeholder --
        # this test also checks the list endpoint below, which excludes
        # browser-test calls by default (include_test_calls=False), and
        # that filtering isn't what this test is about.
        caller_number="+919876543210",
        status="completed",
        started_at=started,
        ended_at=started + timedelta(minutes=4, seconds=30),
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    lead = Lead(user_id=test_user.id, call_session_id=session.id, guest_name="Rohan", phone="9123456780")
    db_session.add(lead)
    await db_session.commit()
    await db_session.refresh(lead)
    # CallSession.guest_name/.guest_phone read through CallSession.lead,
    # which navigates via CallSession.lead_id -- not Lead.call_session_id
    # (see call_session.py's own comment: this lets many call_sessions
    # share one lead for a repeat caller). Setting Lead.call_session_id
    # above links the lead back to this call for lookups the other
    # direction, but doesn't by itself make this the call's *current* lead.
    session.lead_id = lead.id
    await db_session.commit()

    resp = await client.get(f"/api/v1/calls/{session.id}", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["guest_name"] == "Rohan"
    assert body["guest_phone"] == "9123456780"
    assert body["duration_minutes"] == 4.5

    list_resp = await client.get("/api/v1/calls", headers=auth_headers)
    assert list_resp.status_code == 200
    matching = next(c for c in list_resp.json() if c["id"] == str(session.id))
    assert matching["guest_name"] == "Rohan"
    assert matching["duration_minutes"] == 4.5


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
