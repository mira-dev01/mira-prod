import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote_plus

import respx
from httpx import Response

from app.config import settings
from app.models.guest_profile import GuestProfile
from app.models.lead import Lead
from app.models.property import Property
from app.services import call_coordinator, lead_service, recovery_service


async def _busy_lead(db_session, test_property, phone="+919999900001"):
    # handle_busy_recovery itself fires its OWN guest-facing WhatsApp send
    # (the "we missed your call" numbered menu) via a detached
    # asyncio.create_task, unrelated to availability recovery -- when a
    # test also mocks Twilio (see _mock_twilio), that background send can
    # land on the exact same mocked endpoint. A short sleep lets that
    # pre-existing, unrelated task actually run and settle before the test
    # body starts asserting on the mocked route, so assertions about
    # availability-notification sends aren't polluted by this unrelated,
    # already-existing send. _availability_calls() below additionally
    # filters by message content, as a second, content-based guard against
    # the same cross-contamination.
    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number=phone,
        dialed_number=test_property.exophone,
    )
    assert metadata is not None
    await asyncio.sleep(0.05)
    lead = await db_session.get(Lead, metadata.lead_id)
    await db_session.refresh(lead)
    return lead


def _mock_twilio(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "test-sid")
    monkeypatch.setattr(settings, "twilio_auth_token", "test-token")
    monkeypatch.setattr(settings, "twilio_availability_template_sid", None)  # plain-text path by default
    return respx.post("https://api.twilio.com/2010-04-01/Accounts/test-sid/Messages.json").mock(
        return_value=Response(200, json={"sid": "SM123", "status": "queued"})
    )


def _availability_calls(route) -> list:
    """Filters a mocked Twilio route's calls down to just the availability
    ("Mira is available now") sends -- excludes handle_busy_recovery's own
    unrelated guest/host sends that can land on the same mocked endpoint
    (see _busy_lead's own comment). unquote_plus: send_whatsapp_message's
    request body is form-urlencoded (spaces as '+'/'%20'), not literal
    text."""
    return [
        call for call in route.calls if "available now" in unquote_plus(call.request.content.decode())
    ]


async def test_busy_caller_becomes_pending_recovery(test_property, db_session):
    lead = await _busy_lead(db_session, test_property)

    assert lead.recovery_reason == "BUSY_CALL"
    assert lead.busy_recovery_availability_status == "pending"
    assert lead.busy_recovery_at is not None
    # Sales lifecycle stays completely untouched by recovery bookkeeping.
    assert lead.status == "open"


@respx.mock
async def test_pending_busy_caller_receives_availability_whatsapp(test_property, db_session, monkeypatch):
    route = _mock_twilio(monkeypatch)
    lead = await _busy_lead(db_session, test_property)

    await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)

    calls = _availability_calls(route)
    assert len(calls) == 1

    await db_session.refresh(lead)
    assert lead.busy_recovery_availability_status == "notified"
    assert lead.busy_recovery_claimed_at is None


@respx.mock
async def test_sent_availability_message_creates_a_dashboard_notification(test_property, db_session, monkeypatch):
    # Regression: a successful availability send must create a real
    # Notification row (channel=availability_notification) -- without one,
    # the dashboard/SSE stream has no way to ever show the "Availability
    # message sent" stage of the funnel, even though the guest really did
    # receive it.
    from sqlalchemy import select

    from app.models.notification import Notification

    _mock_twilio(monkeypatch)
    lead = await _busy_lead(db_session, test_property)

    await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)

    notification = await db_session.scalar(
        select(Notification).where(
            Notification.lead_id == lead.id,
            Notification.channel == recovery_service.NOTIFICATION_CHANNEL_AVAILABILITY,
        )
    )
    assert notification is not None
    assert notification.property_id == test_property.id


@respx.mock
async def test_multiple_pending_callers_all_receive_availability_whatsapp(test_property, db_session, monkeypatch):
    route = _mock_twilio(monkeypatch)
    lead_b = await _busy_lead(db_session, test_property, phone="+919999900002")
    lead_c = await _busy_lead(db_session, test_property, phone="+919999900003")
    lead_d = await _busy_lead(db_session, test_property, phone="+919999900004")

    await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)

    assert len(_availability_calls(route)) == 3
    for lead in (lead_b, lead_c, lead_d):
        await db_session.refresh(lead)
        assert lead.busy_recovery_availability_status == "notified"


@respx.mock
async def test_already_converted_guest_does_not_receive_availability_message(test_property, db_session, monkeypatch):
    route = _mock_twilio(monkeypatch)
    lead = await _busy_lead(db_session, test_property)
    lead.status = "booked"  # host converted this lead -- sales lifecycle, untouched by recovery bookkeeping
    await db_session.commit()

    await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)

    assert _availability_calls(route) == []
    await db_session.refresh(lead)
    # Left "pending" (not force-marked "notified") -- the claim query's own
    # WHERE clause simply never selected this lead as a candidate at all,
    # since status is no longer in _REUSABLE_LEAD_STATUSES.
    assert lead.busy_recovery_availability_status == "pending"


@respx.mock
async def test_guest_already_engaged_on_whatsapp_does_not_receive_duplicate_message(
    test_property, db_session, monkeypatch
):
    route = _mock_twilio(monkeypatch)
    lead = await _busy_lead(db_session, test_property)
    # Simulate whatsapp_reply_service._notify_host_of_reply having already
    # run for this lead -- the real signal reused here (see
    # recovery_service._AWAITING_CALLBACK_FOLLOW_UP's own comment).
    lead.next_follow_up = "Guest replied on WhatsApp -- call or message them back"
    await db_session.commit()

    await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)

    assert _availability_calls(route) == []
    await db_session.refresh(lead)
    assert lead.busy_recovery_availability_status == "notified"  # claimed, then correctly skipped -- not retried
    assert lead.busy_recovery_claimed_at is None


@respx.mock
async def test_already_notified_recovery_does_not_receive_duplicate_message(test_property, db_session, monkeypatch):
    route = _mock_twilio(monkeypatch)
    lead = await _busy_lead(db_session, test_property)

    await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)
    assert len(_availability_calls(route)) == 1

    # Mira becomes free again a second time (e.g. handled one more call) --
    # must not re-notify an already-notified lead.
    await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)
    assert len(_availability_calls(route)) == 1


async def test_whatsapp_failure_preserves_pending_recovery(test_property, db_session, monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "test-sid")
    monkeypatch.setattr(settings, "twilio_auth_token", "test-token")
    monkeypatch.setattr(settings, "twilio_availability_template_sid", None)
    lead = await _busy_lead(db_session, test_property)

    with respx.mock:
        respx.post("https://api.twilio.com/2010-04-01/Accounts/test-sid/Messages.json").mock(
            return_value=Response(500, text="server error")
        )
        await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)

    await db_session.refresh(lead)
    # Retained as "pending" (not stuck "processing", not falsely "notified")
    # -- eligible for a later retry, exactly per the required contract.
    assert lead.busy_recovery_availability_status == "pending"
    assert lead.busy_recovery_claimed_at is None


@respx.mock
async def test_retry_can_eventually_send_after_a_prior_failure(test_property, db_session, monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "test-sid")
    monkeypatch.setattr(settings, "twilio_auth_token", "test-token")
    monkeypatch.setattr(settings, "twilio_availability_template_sid", None)
    lead = await _busy_lead(db_session, test_property)

    route = respx.post("https://api.twilio.com/2010-04-01/Accounts/test-sid/Messages.json")
    route.side_effect = [
        Response(500, text="server error"),
        Response(200, json={"sid": "SM456", "status": "queued"}),
    ]

    await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)
    await db_session.refresh(lead)
    assert lead.busy_recovery_availability_status == "pending"

    await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)
    await db_session.refresh(lead)
    assert lead.busy_recovery_availability_status == "notified"
    assert route.call_count == 2


async def test_lease_release_does_not_await_availability_processing(test_property):
    # Regression for "must not add latency to call termination": release()
    # itself must return immediately regardless of how slow a subsequent
    # WhatsApp send would be -- this test proves release() has no
    # awareness of/dependency on recovery_service at all (see
    # call_coordinator.py's own docstring), by calling it directly with no
    # recovery_service import in the loop and confirming it's fast and
    # returns a plain bool.
    lease = await call_coordinator.acquire(test_property.user_id, test_property.id, holder_ref="call-x")
    assert lease is not None

    start = asyncio.get_event_loop().time()
    released = await call_coordinator.release(test_property.user_id, test_property.id, lease.token)
    elapsed = asyncio.get_event_loop().time() - start

    assert released is True
    assert elapsed < 0.5  # real Redis round trip only -- no WhatsApp/DB work of any kind


@respx.mock
async def test_call_teardown_is_unaffected_by_availability_whatsapp_failure(test_property, db_session, monkeypatch):
    # process_availability_recovery must never raise into its caller
    # (_run_pipeline's finally block) even when the WhatsApp send inside it
    # fails outright.
    monkeypatch.setattr(settings, "twilio_account_sid", "test-sid")
    monkeypatch.setattr(settings, "twilio_auth_token", "test-token")
    monkeypatch.setattr(settings, "twilio_availability_template_sid", None)
    respx.post("https://api.twilio.com/2010-04-01/Accounts/test-sid/Messages.json").mock(
        side_effect=Exception("network exploded")
    )
    await _busy_lead(db_session, test_property)

    # Must not raise.
    await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)


async def test_duplicate_lease_release_does_not_duplicate_messages(test_property):
    # Regression: release() is idempotent -- a second release() call with a
    # stale/already-released token returns False, so pipeline.py's own
    # "only trigger on True" gate (not this module) is what actually
    # prevents a duplicate trigger; this test proves that gate's premise --
    # a second release() call for the same already-released lease returns
    # False, not True.
    lease = await call_coordinator.acquire(test_property.user_id, test_property.id, holder_ref="call-y")
    assert lease is not None

    first = await call_coordinator.release(test_property.user_id, test_property.id, lease.token)
    second = await call_coordinator.release(test_property.user_id, test_property.id, lease.token)

    assert first is True
    assert second is False


@respx.mock
async def test_concurrent_availability_processing_does_not_double_send(test_property, db_session, monkeypatch):
    # The actual concurrency-safety property: two "workers" (simulated as
    # two concurrent calls to process_availability_recovery, matching how
    # two independent lease-release events for the same host/property
    # could plausibly race) must claim-and-send exactly once between them,
    # never twice, for the same lead.
    route = _mock_twilio(monkeypatch)
    await _busy_lead(db_session, test_property)

    await asyncio.gather(
        recovery_service.process_availability_recovery(test_property.user_id, test_property.id),
        recovery_service.process_availability_recovery(test_property.user_id, test_property.id),
    )

    assert len(_availability_calls(route)) == 1


async def test_expired_recovery_does_not_receive_stale_availability_message(test_property, db_session, monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "test-sid")
    monkeypatch.setattr(settings, "twilio_auth_token", "test-token")
    monkeypatch.setattr(settings, "twilio_availability_template_sid", None)
    lead = await _busy_lead(db_session, test_property)
    # Backdate past AVAILABILITY_WINDOW (30 minutes) -- simulates Mira
    # staying busy far longer than the "call back in 5 mins" premise
    # assumes.
    lead.busy_recovery_at = datetime.now(timezone.utc) - recovery_service.AVAILABILITY_WINDOW - timedelta(minutes=5)
    await db_session.commit()

    with respx.mock:
        route = respx.post("https://api.twilio.com/2010-04-01/Accounts/test-sid/Messages.json").mock(
            return_value=Response(200, json={"sid": "SM789", "status": "queued"})
        )
        await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)
        assert _availability_calls(route) == []

    await db_session.refresh(lead)
    assert lead.busy_recovery_availability_status == "pending"  # never selected as a candidate, never touched


async def test_zero_pending_recoveries_is_a_clean_no_op(test_property):
    # Mira becomes free with no one waiting -- must not raise or do
    # anything surprising.
    await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)


async def test_stale_processing_claim_is_reclaimable_after_worker_crash(test_property, db_session, monkeypatch):
    # Crash recovery: a worker that claimed a lead (pending -> processing)
    # and died before sending must not leave that guest permanently
    # un-notified.
    lead = await _busy_lead(db_session, test_property)
    lead.busy_recovery_availability_status = "processing"
    lead.busy_recovery_claimed_at = (
        datetime.now(timezone.utc) - recovery_service.STALE_CLAIM_THRESHOLD - timedelta(seconds=30)
    )
    await db_session.commit()

    with respx.mock:
        monkeypatch.setattr(settings, "twilio_account_sid", "test-sid")
        monkeypatch.setattr(settings, "twilio_auth_token", "test-token")
        monkeypatch.setattr(settings, "twilio_availability_template_sid", None)
        route = respx.post("https://api.twilio.com/2010-04-01/Accounts/test-sid/Messages.json").mock(
            return_value=Response(200, json={"sid": "SM321", "status": "queued"})
        )
        await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)
        assert len(_availability_calls(route)) == 1

    await db_session.refresh(lead)
    assert lead.busy_recovery_availability_status == "notified"


async def test_fresh_processing_claim_is_not_reclaimed_while_still_in_flight(test_property, db_session, monkeypatch):
    # The inverse of the crash-recovery test: a "processing" row that was
    # JUST claimed (not stale) must NOT be reclaimed/re-sent by a second
    # concurrent run -- only genuinely abandoned claims are fair game.
    lead = await _busy_lead(db_session, test_property)
    lead.busy_recovery_availability_status = "processing"
    lead.busy_recovery_claimed_at = datetime.now(timezone.utc)  # claimed just now, well within the staleness window
    await db_session.commit()

    with respx.mock:
        monkeypatch.setattr(settings, "twilio_account_sid", "test-sid")
        monkeypatch.setattr(settings, "twilio_auth_token", "test-token")
        monkeypatch.setattr(settings, "twilio_availability_template_sid", None)
        route = respx.post("https://api.twilio.com/2010-04-01/Accounts/test-sid/Messages.json").mock(
            return_value=Response(200, json={"sid": "SM654", "status": "queued"})
        )
        await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)
        assert _availability_calls(route) == []

    await db_session.refresh(lead)
    assert lead.busy_recovery_availability_status == "processing"  # untouched -- still in flight, not our claim


@respx.mock
async def test_property_scoping_uses_the_guests_current_property_not_a_stale_notification(
    test_property, db_session, test_user, monkeypatch
):
    # Regression: candidate scoping is joined through
    # GuestProfile.last_property_id (the guest's MOST RECENT busy call),
    # not a Notification.property_id subquery. A guest busy-rejected for
    # Property A, then later busy-rejected again for Property B of the SAME
    # host, reuses the SAME open Lead (upsert_lead's reuse is keyed by
    # guest_profile_id only, not property) -- so this Lead now has TWO
    # busy_recovery Notifications (one per property) but only one CURRENT
    # property (B, the most recent). A Notification-based join could
    # wrongly match on the stale Property A Notification; this test proves
    # it doesn't -- releasing Property A's lease must NOT notify this guest
    # (their current wait is for Property B).
    route = _mock_twilio(monkeypatch)
    property_b = Property(
        user_id=test_user.id,
        name="Second Villa",
        city="Goa",
        exophone=f"+9180{uuid.uuid4().int % 10**8:08d}",
        base_price=5000,
        house_rules="No smoking.",
        max_guests=4,
    )
    db_session.add(property_b)
    await db_session.commit()
    await db_session.refresh(property_b)

    phone = "+919999900099"
    lead_a = await _busy_lead(db_session, test_property, phone=phone)  # busy call #1: Property A

    metadata_b = await recovery_service.handle_busy_recovery(
        host_user_id=test_user.id,
        property_id=property_b.id,
        caller_number=phone,
        dialed_number=property_b.exophone,
    )
    await asyncio.sleep(0.05)
    assert metadata_b.lead_id == lead_a.id  # confirms the same-Lead-reuse premise this test depends on

    # Mira frees up for Property A -- this guest's CURRENT wait is for B,
    # not A, so no availability message should go out yet.
    await recovery_service.process_availability_recovery(test_user.id, test_property.id)
    assert _availability_calls(route) == []

    # Mira frees up for Property B -- now it should send.
    await recovery_service.process_availability_recovery(test_user.id, property_b.id)
    assert len(_availability_calls(route)) == 1


async def test_lead_reused_by_a_real_answered_call_no_longer_receives_availability_message(
    test_property, db_session, test_user
):
    # Regression: a busy-recovery Lead reused by a REAL, answered call (not
    # another busy rejection) must stop being eligible for the "available
    # now" follow-up -- the guest got through to Mira for real on this
    # exact Lead. Deliberately does NOT rely on the agent having called
    # update_lead with a next_follow_up during that real call (many calls
    # resolve without one) -- upsert_lead itself clears
    # busy_recovery_availability_status whenever a real call_session_id
    # reuses a lead still armed for it.
    lead = await _busy_lead(db_session, test_property)
    assert lead.busy_recovery_availability_status == "pending"

    guest = await db_session.get(GuestProfile, lead.guest_profile_id)

    # Simulate the guest calling back and Mira actually answering this
    # time -- a real call reaching lead_service.upsert_lead with a genuine
    # call_session_id, reusing the SAME Lead (guest_profile_id match).
    # Deliberately does NOT pass next_follow_up, matching a call where the
    # agent resolves the guest's question without an explicit update_lead
    # follow-up note.
    from app.models.call_session import CallSession

    session = CallSession(
        exotel_call_id=f"call-{uuid.uuid4().hex[:8]}",
        user_id=test_user.id,
        property_id=test_property.id,
        caller_number=lead.phone,
        status="in_progress",
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    await lead_service.upsert_lead(
        db_session,
        test_user.id,
        call_session_id=session.id,
        guest_profile_id=guest.id,
        conversation_summary="Guest asked about check-in time, resolved on the call.",
    )

    await db_session.refresh(lead)
    assert lead.busy_recovery_availability_status is None
    assert lead.busy_recovery_claimed_at is None
    # recovery_reason is history, not follow-up state -- stays true even
    # after the guest gets through for real.
    assert lead.recovery_reason == "BUSY_CALL"
