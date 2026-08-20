"""call_summary_notification.py -- the host-facing end-of-call WhatsApp
(property inquired, guest name, guest count, check-in/out, whether
escalation was raised, and a short recap). notify_host_of_call_summary
opens its own DB session (same shape as recovery_service.handle_busy_recovery/
guest_calling_notification.maybe_notify_guest_calling), so these tests call
it directly rather than through the pipeline, writing ai_summary via the
fixture's own db_session first (visible across sessions against the same
real test DB, same pattern test_guest_calling_notification.py already
relies on)."""

from urllib.parse import unquote_plus

import respx
from httpx import Response

from app.config import settings
from app.services import call_summary_notification
from app.services.notification_service import create_notification

_SUMMARY = {
    "booking_snapshot": {
        "guest_name": "Riya Sharma",
        "intent": "New Booking",
        "check_in": "2026-09-10",
        "check_out": "2026-09-13",
        "nights": "3",
        "guests": "4",
        "property": ["Test Villa"],
        "budget": "Not mentioned",
        "occasion": "Not mentioned",
        "language": "English",
        "room_number": "Not mentioned",
    },
    "conversation_summary": "Guest asked about weekend availability and pricing for Test Villa.",
    "outcome": {"status": "Booking Likely", "reason": "Guest confirmed interest."},
    "host_action": ["Follow up to confirm booking."],
    "key_details": [],
    "missing_information": [],
}


def _mock_twilio(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "test-sid")
    monkeypatch.setattr(settings, "twilio_auth_token", "test-token")
    monkeypatch.setattr(settings, "twilio_whatsapp_from", "whatsapp:+15550001111")
    monkeypatch.setattr(settings, "twilio_call_summary_template_sid", None)  # plain-text path
    return respx.post("https://api.twilio.com/2010-04-01/Accounts/test-sid/Messages.json").mock(
        return_value=Response(200, json={"sid": "SM123", "status": "queued"})
    )


@respx.mock
async def test_sends_summary_with_expected_fields_and_no_escalation(
    test_property, test_call_session, test_user, db_session, monkeypatch
):
    route = _mock_twilio(monkeypatch)
    test_user.phone = "+919812345678"
    test_call_session.ai_summary = _SUMMARY
    await db_session.commit()

    await call_summary_notification.notify_host_of_call_summary(
        test_call_session.id, test_user.id, test_property.name
    )

    assert len(route.calls) == 1
    body = unquote_plus(route.calls[0].request.content.decode())
    assert "Test Villa" in body
    assert "Riya Sharma" in body
    assert "4" in body
    assert "2026-09-10" in body
    assert "2026-09-13" in body
    assert "Escalation raised:* No" in body
    assert str(test_call_session.id) in body


@respx.mock
async def test_reports_escalation_raised_when_an_escalation_notification_exists(
    test_property, test_call_session, test_user, db_session, monkeypatch
):
    route = _mock_twilio(monkeypatch)
    test_user.phone = "+919812345678"
    test_call_session.ai_summary = _SUMMARY
    await db_session.commit()

    await create_notification(
        db_session,
        channel="escalation",
        property_id=test_property.id,
        call_session_id=test_call_session.id,
        urgency="high",
        message="AC not working",
    )

    await call_summary_notification.notify_host_of_call_summary(
        test_call_session.id, test_user.id, test_property.name
    )

    body = unquote_plus(route.calls[0].request.content.decode())
    assert "Escalation raised:* Yes" in body


@respx.mock
async def test_skipped_when_host_has_no_phone(test_property, test_call_session, test_user, db_session, monkeypatch):
    route = _mock_twilio(monkeypatch)
    test_user.phone = None
    test_call_session.ai_summary = _SUMMARY
    await db_session.commit()

    await call_summary_notification.notify_host_of_call_summary(
        test_call_session.id, test_user.id, test_property.name
    )

    assert len(route.calls) == 0


@respx.mock
async def test_skipped_when_no_ai_summary_yet(test_property, test_call_session, test_user, db_session, monkeypatch):
    route = _mock_twilio(monkeypatch)
    test_user.phone = "+919812345678"
    await db_session.commit()

    await call_summary_notification.notify_host_of_call_summary(
        test_call_session.id, test_user.id, test_property.name
    )

    assert len(route.calls) == 0
