"""SPACEZ ONLY. A guest with a confirmed booking rings back for an early check-in / late check-out: the agent
tells them the charge, the team gets a ticket and answers yes or no, and the guest is sent a payment link (yes)
or a "we can't" message (no) on WhatsApp. On the test services (no real money or guests)."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from tests import fake_providers as sim
from tests.fake_providers import fake_providers_on  # noqa: F401  (autouse: fake payment provider + WhatsApp)
from app.models.notification import Notification
from app.models.payment import PaymentRequest
from app.models.property import Property
from app.models.spacez_stay_request import SpacezStayRequest
from app.models.ticket import Ticket
from app.models.user import User
from app.schemas.agent_config import AgentConfig, BookingConfig, PaymentConfig
from app.schemas.tool import GetPricingArgs
from app.services import spacez_stay_requests, tool_handlers
from app.voice.conversation_state import ConversationState
from tests.conftest import auth_headers_for

CALLER = "+919876543210"  # how the telephony number looks; the booking has the bare 10 digits


def _tue() -> date:
    d = date.today() + timedelta(days=21)
    while d.weekday() != 1:
        d += timedelta(days=1)
    return d


@pytest.fixture
async def booked(db_session, test_user, test_call_session):
    """A host with a Spacez villa and a guest whose booking is PAID."""
    user = await db_session.get(User, test_user.id)
    user.billing_config = sim.billing_config()
    user.agent_config = AgentConfig(payment=PaymentConfig(hold_minutes=10), booking=BookingConfig(confirm_by="payment")).model_dump()
    prop = Property(
        user_id=user.id, name="Deccan Escape", city="Hyderabad", base_price=7000, max_guests=12,
        exophone=f"+9180{uuid.uuid4().int % 10**8:08d}", pricing_terms={"base_guests": 8},
    )
    db_session.add(prop)
    await db_session.commit()
    await db_session.refresh(prop)

    user_id, prop_id, call_id = user.id, prop.id, test_call_session.id  # survive db.expire_all()
    state = ConversationState()
    args = GetPricingArgs(property_id=str(prop.id), check_in=_tue(), check_out=_tue() + timedelta(days=2), num_guests=8)
    await tool_handlers.handle_get_pricing(
        db_session, args, user.id,
        on_priced=lambda p, b: state.record_quoted_price(p.name, args.check_in.isoformat(), args.check_out.isoformat(), b.total, nights=b.nights, bill=b.bill),
    )
    await tool_handlers.handle_send_payment_link(
        db_session, host_user_id=user.id, property_id=str(prop.id), quote=state.quoted_price, call_session_id=test_call_session.id,
        guest_name="Asha Rao", phone="9876543210", email="asha@example.com", purpose_of_stay="a birthday party", payment_option="full",
    )
    request = await db_session.scalar(select(PaymentRequest).where(PaymentRequest.user_id == user.id))
    await sim.set_outcome(request.razorpay_link_id, "paid")
    await tool_handlers.handle_get_payment_status(db_session, host_user_id=user.id, call_session_id=test_call_session.id)
    db_session.expire_all()

    class Ctx:
        pass

    ctx = Ctx()
    ctx.user_id, ctx.prop_id, ctx.call = user_id, prop_id, call_id
    return ctx


async def _ask(db, ctx, phone=CALLER, **kw):
    kw.setdefault("early_checkin", True)
    kw.setdefault("late_checkout", False)
    return await spacez_stay_requests.handle_request(db, host_user_id=ctx.user_id, call_session_id=ctx.call, guest_phone=phone, **kw)


async def _messages(db) -> list[str]:
    return sim.message_bodies()


async def _row(db, ctx) -> SpacezStayRequest:
    return await db.scalar(select(SpacezStayRequest).where(SpacezStayRequest.user_id == ctx.user_id).order_by(SpacezStayRequest.created_at.desc()))


# -- the guest asks on the call -----------------------------------------------------------------------


async def test_the_guest_hears_the_charge_and_that_the_team_will_check(db_session, booked):
    result = await _ask(db_session, booked, note="around 10 am")

    assert "₹1,000" in result and "early check-in" in result
    assert "let the team check whether it is possible" in result and "payment link on WhatsApp" in result
    assert "Never say it is confirmed" in result
    row = await _row(db_session, booked)
    assert row.status == "pending" and row.want_early_checkin and not row.want_late_checkout and row.early_fee == 1000 and row.note == "around 10 am"


async def test_the_team_gets_a_ticket_and_an_alert(db_session, booked):
    await _ask(db_session, booked, late_checkout=True)

    ticket = await db_session.scalar(select(Ticket).where(Ticket.user_id == booked.user_id, Ticket.kind == "stay_request"))
    alert = await db_session.scalar(select(Notification).where(Notification.channel == "escalation", Notification.property_id == booked.prop_id))
    assert ticket is not None and ticket.status == "open" and ticket.urgency == "high" and "early check-in and late check-out" in ticket.detail
    assert alert is not None and "₹2,000" in alert.message
    assert (await _row(db_session, booked)).ticket_id == ticket.id


async def test_both_charges_are_quoted_separately(db_session, booked):
    result = await _ask(db_session, booked, late_checkout=True)

    assert "₹2,000" in result and "₹1,000 early check-in, ₹1,000 late check-out" in result


async def test_a_number_with_no_confirmed_booking_is_not_offered_anything(db_session, booked):
    result = await _ask(db_session, booked, phone="+919000000000")

    assert "can't find a confirmed booking" in result
    assert await _row(db_session, booked) is None


async def test_asking_for_neither_is_refused(db_session, booked):
    assert "early check-in, a late check-out, or both" in await _ask(db_session, booked, early_checkin=False)


async def test_asking_twice_is_one_request(db_session, booked):
    await _ask(db_session, booked)
    again = await _ask(db_session, booked)

    assert "already asked" in again and "Do not raise it again" in again
    assert len((await db_session.scalars(select(SpacezStayRequest))).all()) == 1


# -- the team answers -----------------------------------------------------------------------------------


async def test_yes_sends_the_guest_a_payment_link_for_the_flat_fee(client, db_session, booked):
    await _ask(db_session, booked)
    ticket = await db_session.scalar(select(Ticket).where(Ticket.kind == "stay_request"))

    answered = await client.post(f"/api/v1/spacez/stay-requests/{ticket.id}/approve", headers=auth_headers_for(await db_session.get(User, booked.user_id)))

    assert answered.status_code == 200 and answered.json() == {"status": "awaiting_payment"}
    row = await _row(db_session, booked)
    assert row.status == "awaiting_payment" and row.short_url and row.razorpay_link_id
    message = (await _messages(db_session))[-1]
    assert "arrange early check-in" in message and "₹1,000" in message and row.short_url in message and message.startswith("Hi Asha!")
    await db_session.refresh(ticket)
    assert ticket.status == "done"


async def test_the_fee_payment_is_noticed_and_the_guest_thanked(client, db_session, booked):
    await _ask(db_session, booked)
    ticket = await db_session.scalar(select(Ticket).where(Ticket.kind == "stay_request"))
    await client.post(f"/api/v1/spacez/stay-requests/{ticket.id}/approve", headers=auth_headers_for(await db_session.get(User, booked.user_id)))
    row = await _row(db_session, booked)

    assert await spacez_stay_requests.sweep(db_session) == 0  # not paid yet
    await sim.set_outcome(row.razorpay_link_id, "paid")
    assert await spacez_stay_requests.sweep(db_session) == 1
    assert await spacez_stay_requests.sweep(db_session) == 0  # exactly once

    db_session.expire_all()
    row = await _row(db_session, booked)
    assert row.status == "paid" and row.paid_at is not None
    assert "received ₹1,000 for early check-in" in (await _messages(db_session))[-1]


async def test_a_lapsed_fee_link_is_marked_expired(client, db_session, booked):
    await _ask(db_session, booked)
    ticket = await db_session.scalar(select(Ticket).where(Ticket.kind == "stay_request"))
    await client.post(f"/api/v1/spacez/stay-requests/{ticket.id}/approve", headers=auth_headers_for(await db_session.get(User, booked.user_id)))
    row = await _row(db_session, booked)

    await sim.set_outcome(row.razorpay_link_id, "expired")
    await spacez_stay_requests.sweep(db_session)

    db_session.expire_all()
    assert (await _row(db_session, booked)).status == "expired"


async def test_no_tells_the_guest_on_whatsapp_the_team_cannot(client, db_session, booked):
    await _ask(db_session, booked, early_checkin=False, late_checkout=True)
    ticket = await db_session.scalar(select(Ticket).where(Ticket.kind == "stay_request"))
    headers = auth_headers_for(await db_session.get(User, booked.user_id))

    answered = await client.post(f"/api/v1/spacez/stay-requests/{ticket.id}/decline", headers=headers)

    assert answered.json() == {"status": "declined"}
    message = (await _messages(db_session))[-1]
    assert "won't be able to arrange late check-out" in message and "stays exactly as it is" in message
    assert "http" not in message  # no payment link
    again = await client.post(f"/api/v1/spacez/stay-requests/{ticket.id}/approve", headers=headers)
    assert again.status_code == 422 and "already been answered" in again.json()["detail"]


async def test_another_host_cannot_answer_the_request(client, db_session, booked):
    await _ask(db_session, booked)
    ticket = await db_session.scalar(select(Ticket).where(Ticket.kind == "stay_request"))
    stranger = User(email=f"x-{uuid.uuid4().hex[:6]}@example.com", clerk_user_id=f"user_{uuid.uuid4().hex[:12]}", name="Other")
    db_session.add(stranger)
    await db_session.commit()

    answered = await client.post(f"/api/v1/spacez/stay-requests/{ticket.id}/approve", headers=auth_headers_for(stranger))

    assert answered.status_code == 422 and "not found" in answered.json()["detail"]
    assert (await _row(db_session, booked)).status == "pending"


async def test_a_payment_service_outage_puts_the_request_back_for_another_try(client, db_session, booked):
    await _ask(db_session, booked)
    ticket_id = (await db_session.scalar(select(Ticket).where(Ticket.kind == "stay_request"))).id
    headers = auth_headers_for(await db_session.get(User, booked.user_id))
    await sim.set_down("payment", True)

    failed = await client.post(f"/api/v1/spacez/stay-requests/{ticket_id}/approve", headers=headers)
    await sim.set_down("payment", False)

    assert failed.status_code == 422 and "could not be created" in failed.json()["detail"]
    db_session.expire_all()
    assert (await _row(db_session, booked)).status == "pending"
    retried = await client.post(f"/api/v1/spacez/stay-requests/{ticket_id}/approve", headers=headers)
    assert retried.json() == {"status": "awaiting_payment"}


async def test_a_call_back_from_the_number_the_guest_booked_from_is_recognised_too(db_session, booked):
    """The link may go to a different WhatsApp number than the one the guest rang from; both find the booking."""
    request = await db_session.scalar(select(PaymentRequest).where(PaymentRequest.user_id == booked.user_id))
    request.details = {**request.details, "caller_phone": "+918888877777"}
    await db_session.commit()

    from_booking_number = await _ask(db_session, booked, phone="+919876543210")
    from_calling_number = await _ask(db_session, booked, phone="+918888877777", early_checkin=False, late_checkout=True)

    assert "Logged for the team" in from_booking_number and "Logged for the team" in from_calling_number


async def test_with_the_template_sids_set_all_three_stay_messages_go_as_templates(client, db_session, booked, monkeypatch):
    # A wrong number of values would silently fall back to free text, so check each goes out as its template.
    from app.services import whatsapp_templates as wt

    for name in ("SPACEZ_STAY_FEE_LINK_SID", "SPACEZ_STAY_REQUEST_DECLINED_SID", "SPACEZ_STAY_FEE_RECEIVED_SID"):
        monkeypatch.setattr(wt, name, "HX" + name[-6:])
    headers = auth_headers_for(await db_session.get(User, booked.user_id))

    await _ask(db_session, booked)
    ticket = await db_session.scalar(select(Ticket).where(Ticket.kind == "stay_request"))
    await client.post(f"/api/v1/spacez/stay-requests/{ticket.id}/approve", headers=headers)
    link = sim.messages()[-1]
    assert link.kind == "template" and len(link.variables) == len(wt.TEMPLATES["spacez_stay_fee_link"].variables) == 6
    assert link.variables["5"] == "₹1,000" and link.variables["6"].startswith("https://rzp.test/pay/")

    row = await _row(db_session, booked)
    await sim.set_outcome(row.razorpay_link_id, "paid")
    await spacez_stay_requests.sweep(db_session)
    received = sim.messages()[-1]
    assert received.kind == "template" and len(received.variables) == 5 and received.variables["2"] == "₹1,000"
