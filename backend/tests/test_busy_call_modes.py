"""Agent page -> Busy calls: what happens to a guest who rang while Mira was on a call."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import respx
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.call_session import CallSession
from app.models.guest_profile import GuestProfile
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.ticket import Ticket
from app.models.user import User
from app.services import lead_service, recovery_service
from app.config import settings
from tests.test_availability_recovery import _availability_calls, _busy_lead
from tests.test_availability_recovery import _mock_twilio as _mock_twilio_account


def _mock_twilio(monkeypatch):
    """The shared helper only sets the account; with Twilio disabled or no sender
    the send is skipped, so set both here and these tests don't depend on .env."""
    monkeypatch.setattr(settings, "twilio_enabled", True)
    monkeypatch.setattr(settings, "twilio_messaging_service_sid", None)
    monkeypatch.setattr(settings, "twilio_whatsapp_from", "whatsapp:+14155550100")
    return _mock_twilio_account(monkeypatch)


async def _set_busy_config(user_id, **busy):
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        user.agent_config = {"busy_calls": busy}
        await db.commit()


async def _tickets(user_id, kind="missed_call"):
    async with AsyncSessionLocal() as db:
        return list((await db.scalars(select(Ticket).where(Ticket.user_id == user_id, Ticket.kind == kind))).all())


async def _lead(lead_id):
    async with AsyncSessionLocal() as db:
        return await db.get(Lead, lead_id)


async def _waiting_lead(db_session, test_property, minutes_past_due=1):
    """A busy-call guest who was told Mira is free again, whose wait has passed."""
    lead = await _busy_lead(db_session, test_property)
    async with AsyncSessionLocal() as db:
        row = await db.get(Lead, lead.id)
        row.busy_recovery_availability_status = "notified"
        row.busy_recovery_callback_due_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_past_due)
        await db.commit()
    return lead


# ---- callback_invite (the default) ------------------------------------


@respx.mock
async def test_telling_the_guest_mira_is_free_starts_the_callback_clock(test_property, db_session, monkeypatch):
    _mock_twilio(monkeypatch)
    lead = await _busy_lead(db_session, test_property)
    assert (await _lead(lead.id)).busy_recovery_callback_due_at is None  # nothing before the message

    before = datetime.now(timezone.utc)
    await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)

    due = (await _lead(lead.id)).busy_recovery_callback_due_at
    assert due is not None and timedelta(minutes=4, seconds=50) < due - before < timedelta(minutes=5, seconds=10)


@respx.mock
async def test_the_wait_is_the_hosts_choice(test_property, db_session, monkeypatch):
    _mock_twilio(monkeypatch)
    await _set_busy_config(test_property.user_id, mode="callback_invite", callback_wait_minutes=12)
    lead = await _busy_lead(db_session, test_property)
    before = datetime.now(timezone.utc)
    await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)
    due = (await _lead(lead.id)).busy_recovery_callback_due_at
    assert timedelta(minutes=11, seconds=50) < due - before < timedelta(minutes=12, seconds=10)


async def test_a_guest_who_did_not_ring_back_gets_the_host_a_ticket(test_property, db_session):
    lead = await _waiting_lead(db_session, test_property)

    assert await recovery_service.check_missed_callbacks() == 1

    (ticket,) = await _tickets(test_property.user_id)
    assert "didn't call back" in ticket.summary and ticket.status == "open" and ticket.urgency == "medium"
    async with AsyncSessionLocal() as db:
        alert = await db.get(Notification, ticket.notification_id)
    assert alert.channel == "busy_recovery" and alert.lead_id == lead.id
    assert (await _lead(lead.id)).busy_recovery_callback_due_at is None  # claimed


async def test_checking_twice_or_at_once_never_tickets_a_guest_twice(test_property, db_session):
    await _waiting_lead(db_session, test_property)
    results = await asyncio.gather(recovery_service.check_missed_callbacks(), recovery_service.check_missed_callbacks())
    assert sum(results) == 1
    assert await recovery_service.check_missed_callbacks() == 0
    assert len(await _tickets(test_property.user_id)) == 1


async def test_nothing_happens_before_the_wait_is_over(test_property, db_session):
    lead = await _busy_lead(db_session, test_property)
    async with AsyncSessionLocal() as db:
        row = await db.get(Lead, lead.id)
        row.busy_recovery_availability_status = "notified"
        row.busy_recovery_callback_due_at = datetime.now(timezone.utc) + timedelta(minutes=3)
        await db.commit()
    assert await recovery_service.check_missed_callbacks() == 0
    assert await _tickets(test_property.user_id) == []
    assert (await _lead(lead.id)).busy_recovery_callback_due_at is not None  # still waiting


async def test_a_guest_who_rang_back_and_got_through_is_never_ticketed(test_property, test_user, db_session):
    lead = await _waiting_lead(db_session, test_property)
    await db_session.refresh(lead)  # this session still holds the lead as it was before the sweep set it up
    guest = await db_session.get(GuestProfile, lead.guest_profile_id)
    session = CallSession(
        exotel_call_id=f"call-{uuid.uuid4().hex[:8]}", user_id=test_user.id, property_id=test_property.id,
        caller_number=lead.phone, status="in_progress",
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    await lead_service.upsert_lead(db_session, test_user.id, call_session_id=session.id, guest_profile_id=guest.id)

    assert (await _lead(lead.id)).busy_recovery_callback_due_at is None  # cleared by the real call
    assert await recovery_service.check_missed_callbacks() == 0
    assert await _tickets(test_user.id) == []


async def test_a_guest_who_answered_on_whatsapp_is_not_ticketed_as_silent(test_property, db_session):
    lead = await _waiting_lead(db_session, test_property)
    async with AsyncSessionLocal() as db:
        row = await db.get(Lead, lead.id)
        row.next_follow_up = "Guest asked for photos on WhatsApp"  # what whatsapp_reply_service does
        await db.commit()
    assert await recovery_service.check_missed_callbacks() == 0
    assert await _tickets(test_property.user_id) == []
    assert (await _lead(lead.id)).busy_recovery_callback_due_at is None  # settled, not retried forever


# ---- host_follows_up --------------------------------------------------


@respx.mock
async def test_in_host_follows_up_mode_mira_does_not_message_the_guest_again(test_property, db_session, monkeypatch):
    route = _mock_twilio(monkeypatch)
    await _set_busy_config(test_property.user_id, mode="host_follows_up")
    lead = await _busy_lead(db_session, test_property)

    assert (await _lead(lead.id)).busy_recovery_availability_status is None  # not armed for the follow-up
    await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)

    assert _availability_calls(route) == []
    after = await _lead(lead.id)
    assert after.busy_recovery_callback_due_at is None
    (ticket,) = await _tickets(test_property.user_id)
    assert "Call back +919999900001" in ticket.summary and "while you were busy" in ticket.summary
    assert await recovery_service.check_missed_callbacks() == 0  # and no second ticket later


@respx.mock
async def test_switching_to_host_follows_up_disarms_an_older_pending_lead(test_property, db_session, monkeypatch):
    _mock_twilio(monkeypatch)
    lead = await _busy_lead(db_session, test_property)  # default mode: armed
    assert (await _lead(lead.id)).busy_recovery_availability_status == "pending"

    await _set_busy_config(test_property.user_id, mode="host_follows_up")
    await _busy_lead(db_session, test_property)  # the same guest rings again under the new setting
    assert (await _lead(lead.id)).busy_recovery_availability_status is None


async def test_default_hosts_get_no_ticket_at_busy_time(test_property, db_session):
    await _busy_lead(db_session, test_property)
    assert await _tickets(test_property.user_id) == []


@respx.mock
async def test_a_guest_already_queued_is_not_messaged_if_the_host_has_since_switched_modes(
    test_property, db_session, monkeypatch
):
    route = _mock_twilio(monkeypatch)
    lead = await _busy_lead(db_session, test_property)  # queued under the default mode
    assert (await _lead(lead.id)).busy_recovery_availability_status == "pending"

    await _set_busy_config(test_property.user_id, mode="host_follows_up")  # the host changes their mind
    await recovery_service.process_availability_recovery(test_property.user_id, test_property.id)

    assert _availability_calls(route) == []
    after = await _lead(lead.id)
    assert after.busy_recovery_availability_status is None and after.busy_recovery_callback_due_at is None
    (ticket,) = await _tickets(test_property.user_id)
    assert "while you were busy" in ticket.summary
