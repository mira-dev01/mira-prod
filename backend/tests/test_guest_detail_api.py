"""Covers GET /guests/{id}/detail -- specifically the recent_calls[].ai_summary
round-trip, since app/api/v1/guests.py:get_guest_detail constructs
GuestRecentCall by passing the raw JSONB dict (call.ai_summary) straight into
the Pydantic model rather than via model_validate(from_attributes=True), a
different validation path from CallSessionOut's (see test_calls_api.py)."""

from app.models.call_session import CallSession
from app.models.guest_profile import GuestProfile
from app.schemas.call_summary import BookingSnapshot, CallSummary, SummaryOutcome


async def test_guest_detail_recent_calls_include_structured_ai_summary(client, auth_headers, test_user, db_session):
    guest = GuestProfile(phone="+919999999999", host_id=test_user.id, name="Rohan")
    db_session.add(guest)
    await db_session.commit()
    await db_session.refresh(guest)

    summary = CallSummary(
        booking_snapshot=BookingSnapshot(guest_name="Rohan", property=["Villa A"]),
        conversation_summary="Guest asked about a birthday trip to Goa.",
        outcome=SummaryOutcome(status="Awaiting Guest Details", reason="Check-out date not yet confirmed."),
        host_action=["Confirm availability for 29 May."],
        key_details=["Birthday celebration"],
        missing_information=["Check-out date"],
    )
    session = CallSession(
        exotel_call_id="guest-detail-summary-1",
        user_id=test_user.id,
        guest_profile_id=guest.id,
        caller_number="+919999999999",
        status="completed",
        ai_summary=summary.model_dump(),
    )
    db_session.add(session)
    await db_session.commit()

    resp = await client.get(f"/api/v1/guests/{guest.id}/detail", headers=auth_headers)
    assert resp.status_code == 200
    recent_calls = resp.json()["recent_calls"]
    assert len(recent_calls) == 1
    ai_summary = recent_calls[0]["ai_summary"]
    assert ai_summary["conversation_summary"] == "Guest asked about a birthday trip to Goa."
    assert ai_summary["booking_snapshot"]["guest_name"] == "Rohan"
    assert ai_summary["outcome"]["status"] == "Awaiting Guest Details"


async def test_guest_detail_recent_calls_ai_summary_null_when_not_generated(
    client, auth_headers, test_user, db_session
):
    guest = GuestProfile(phone="+918888888888", host_id=test_user.id)
    db_session.add(guest)
    await db_session.commit()
    await db_session.refresh(guest)

    session = CallSession(
        exotel_call_id="guest-detail-summary-2",
        user_id=test_user.id,
        guest_profile_id=guest.id,
        caller_number="+918888888888",
        status="completed",
    )
    db_session.add(session)
    await db_session.commit()

    resp = await client.get(f"/api/v1/guests/{guest.id}/detail", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["recent_calls"][0]["ai_summary"] is None
