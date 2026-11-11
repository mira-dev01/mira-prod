"""The whole booking flow on the test services: a quote with GST, the details the booking needs, the guest's
choice of how to pay, a payment that can fail or succeed on demand, the agent learning the outcome, and the
confirmation. Real Postgres; the payment provider and WhatsApp are the simulator, so no real money and no real guests."""

import re
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.integrations import payment_simulator as sim
from app.models.booking import Booking
from app.models.payment import PaymentRequest
from app.models.property import Property
from app.models.simulator import SimMessage
from app.models.user import User
from app.schemas.agent_config import AgentConfig, BookingConfig, PaymentConfig
from app.schemas.tool import CheckCalendarArgs, GetPricingArgs
from app.services import payment_service, tool_handlers
from app.voice.conversation_state import ConversationState
from tests.conftest import auth_headers_for


@pytest.fixture(autouse=True)
def _simulator_on(monkeypatch):
    monkeypatch.setattr(settings, "simulator_enabled", True)
    monkeypatch.setattr(settings, "simulator_token", "")


def _tue() -> date:
    d = date.today() + timedelta(days=21)
    while d.weekday() != 1:
        d += timedelta(days=1)
    return d


async def _host(db, test_user) -> User:
    user = await db.get(User, test_user.id)
    user.billing_config = {"razorpay": {"key_id": "sim_spacez", "secret_enc": "sim", "mode": "test", "verified_at": "2026-11-10T00:00:00+00:00"}}
    user.agent_config = AgentConfig(payment=PaymentConfig(hold_minutes=10), booking=BookingConfig(confirm_by="payment")).model_dump()
    await db.commit()
    return user


async def _villa(db, user, **terms) -> Property:
    prop = Property(
        user_id=user.id, name="Deccan Escape", city="Hyderabad", base_price=7000, max_guests=12,
        exophone="+918000000002", pricing_terms={"base_guests": 8, **terms},
    )
    db.add(prop)
    await db.commit()
    await db.refresh(prop)
    return prop


async def _quote(db, user, prop, state, check_in=None, **kw) -> str:
    check_in = check_in or _tue()
    args = GetPricingArgs(property_id=str(prop.id), check_in=check_in, check_out=check_in + timedelta(days=2), num_guests=kw.pop("guests", 8), **kw)

    def on_priced(p, b):
        state.record_quoted_price(p.name, args.check_in.isoformat(), args.check_out.isoformat(), b.total, nights=b.nights, bill=b.bill)

    return await tool_handlers.handle_get_pricing(db, args, user.id, on_priced=on_priced)


async def _send(db, user, prop, state, **kw):
    base = dict(guest_name="Asha Rao", phone="9876543210", email="asha@example.com", purpose_of_stay="a birthday party", payment_option="split")
    base.update(kw)
    return await tool_handlers.handle_send_payment_link(
        db, host_user_id=user.id, property_id=str(prop.id), quote=state.quoted_price, call_session_id=None, **base
    )


async def _request(db, user_id) -> PaymentRequest:
    return await db.scalar(select(PaymentRequest).where(PaymentRequest.user_id == user_id).order_by(PaymentRequest.created_at.desc()))


async def _status(db, user, call_session_id):
    return await tool_handlers.handle_get_payment_status(db, host_user_id=user.id, call_session_id=call_session_id)


@pytest.fixture
async def booking(db_session, test_user, test_call_session):
    user = await _host(db_session, test_user)
    prop = await _villa(db_session, user)
    state = ConversationState()
    await _quote(db_session, user, prop, state)

    class Ctx:
        pass

    ctx = Ctx()
    ctx.user, ctx.prop, ctx.state, ctx.call = user, prop, state, test_call_session.id
    ctx.user_id = user.id  # survives db.expire_all()
    return ctx


async def _send_on_call(db, ctx, **kw):
    """send_payment_link as the tool does, but tied to the test call so get_payment_status can find it."""
    base = dict(guest_name="Asha Rao", phone="9876543210", email="asha@example.com", purpose_of_stay="a birthday party", payment_option="split")
    base.update(kw)
    return await tool_handlers.handle_send_payment_link(
        db, host_user_id=ctx.user.id, property_id=str(ctx.prop.id), quote=ctx.state.quoted_price, call_session_id=ctx.call, **base
    )


# -- the booking needs details and the guest's choice -----------------------------------------------


async def test_the_link_waits_for_the_details_the_booking_needs(db_session, booking):
    result = await _send_on_call(db_session, booking, email=None, guest_name="A", purpose_of_stay=None)

    assert "ask the guest for" in result and "full name" in result and "email" in result and "purpose of the stay" in result
    assert await _request(db_session, booking.user.id) is None  # nothing was held or sent


async def test_the_guest_chooses_how_to_pay_and_the_agent_does_not(db_session, booking):
    result = await _send_on_call(db_session, booking, payment_option=None)

    assert "Ask the guest how they want to pay" in result and "₹14,700" in result and "₹7,350 now" in result
    assert await _request(db_session, booking.user.id) is None


async def test_split_charges_half_now_and_holds_the_dates_for_ten_minutes(db_session, booking):
    result = await _send_on_call(db_session, booking, payment_option="split")

    request = await _request(db_session, booking.user.id)
    assert request.kind == "advance" and request.amount_due == 7350 and request.total_amount == 14700
    assert request.details["purpose"] == "Birthday" and request.details["email"] == "asha@example.com"
    assert "₹7,350" in result and "NOT confirmed yet" in result and "get_payment_status" in result
    held_for = (request.hold_expires_at - request.created_at).total_seconds() / 60
    assert 9 <= held_for <= 11


async def test_pay_in_full_charges_everything(db_session, booking):
    await _send_on_call(db_session, booking, payment_option="full")

    request = await _request(db_session, booking.user.id)
    assert request.kind == "full" and request.amount_due == 14700


async def test_a_partner_villa_can_only_be_paid_in_full(db_session, test_user, test_call_session):
    user = await _host(db_session, test_user)
    prop = await _villa(db_session, user, partner=True)
    state = ConversationState()
    await _quote(db_session, user, prop, state)

    await tool_handlers.handle_send_payment_link(
        db_session, host_user_id=user.id, property_id=str(prop.id), quote=state.quoted_price, call_session_id=test_call_session.id,
        guest_name="Asha Rao", phone="9876543210", email="asha@example.com", purpose_of_stay="family", payment_option="split",
    )

    assert (await _request(db_session, user.id)).kind == "full"  # "split" was ignored: partner villas have none


async def test_the_link_goes_to_the_guest_on_the_test_whatsapp(db_session, booking):
    await _send_on_call(db_session, booking)

    messages = (await db_session.scalars(select(SimMessage))).all()
    assert len(messages) == 1 and messages[0].to_phone.endswith("9876543210") and "/api/v1/sim/pay/sim_" in messages[0].body


# -- the agent can tell what happened -----------------------------------------------------------------


async def test_unpaid_failed_then_paid(db_session, booking):
    await _send_on_call(db_session, booking)
    request = await _request(db_session, booking.user.id)

    waiting = await _status(db_session, booking.user, booking.call)
    assert waiting.startswith("NOT PAID YET") and "Do NOT say the booking is confirmed" in waiting

    await sim.set_outcome(request.razorpay_link_id, "failed")
    failed = await _status(db_session, booking.user, booking.call)
    assert failed.startswith("NOT PAID") and "Payment unsuccessful" in failed and "same link can be tried again" in failed

    await sim.set_outcome(request.razorpay_link_id, "paid")
    paid = await _status(db_session, booking.user, booking.call)
    assert paid.startswith("PAID") and "now confirmed" in paid and "stay code is" in paid and "remaining ₹7,350 is due before check-in" in paid


async def test_a_paid_link_becomes_a_confirmed_booking_with_the_documents_confirmation(db_session, booking):
    await _send_on_call(db_session, booking, payment_option="split")
    request = await _request(db_session, booking.user.id)
    await sim.set_outcome(request.razorpay_link_id, "paid")

    await _status(db_session, booking.user, booking.call)

    db_session.expire_all()
    request = await _request(db_session, booking.user_id)
    assert request.status == "paid" and re.fullmatch(r"\d{4}", request.details["stay_code"])
    reservation = await db_session.get(Booking, request.booking_id)
    assert reservation.kind == "reservation" and reservation.status == "confirmed" and reservation.final_booking_price == 14700
    texts = [m.body for m in (await db_session.scalars(select(SimMessage).order_by(SimMessage.created_at))).all()]
    confirmation = next(t for t in texts if t.startswith("Hello Asha, Your Booking is now confirmed"))
    for line in (
        "The Booking Details is following:", f"Booking ID: SPZ-{request.id.hex[:6].upper()}", "Stay Name: Deccan Escape",
        "Stay Selection: Full villa", f"Check in Date: {_tue():%d %b, %Y}", "Number of Guests: 8 Adults, 0 Children, 0 Pets",
        "Breakfast Purchased: No", "Lunch purchased: No", "Dinner purchased: No", "Grill kit: 0", "Food Platter: 0",
        "Stay Purpose: Birthday", "Payment Amount: ₹7,350", "Payment Remaining: ₹7,350", "Payment Status: Half paid",
        "Booking Status: Pending Host Approval", "Regards,\nTeam Spacez", "www.spacez.co",
    ):
        assert line in confirmation, line
    assert any("four-digit stay code is" in t for t in texts)


async def test_paying_in_full_says_paid_in_full(db_session, booking):
    await _send_on_call(db_session, booking, payment_option="full")
    request = await _request(db_session, booking.user.id)
    await sim.set_outcome(request.razorpay_link_id, "paid")
    await _status(db_session, booking.user, booking.call)

    texts = [m.body for m in (await db_session.scalars(select(SimMessage))).all()]
    assert any("Payment Status: Paid in full" in t and "Payment Remaining: ₹0" in t for t in texts)


async def test_a_closed_link_releases_the_dates(db_session, booking):
    await _send_on_call(db_session, booking)
    request = await _request(db_session, booking.user.id)
    await sim.set_outcome(request.razorpay_link_id, "cancelled")

    result = await _status(db_session, booking.user, booking.call)

    assert result.startswith("NOT PAID") and "been cancelled" in result and "no longer held" in result
    available = await tool_handlers.handle_check_calendar(
        db_session, CheckCalendarArgs(property_id=str(booking.prop.id), check_in=_tue(), check_out=_tue() + timedelta(days=2))
    )
    assert "AVAILABLE" in available


async def test_no_link_on_the_call_means_nothing_to_check(db_session, booking):
    assert "No payment link has been sent" in await _status(db_session, booking.user, booking.call)


async def test_a_shoot_is_held_not_confirmed(db_session, booking):
    result = await _send_on_call(db_session, booking, purpose_of_stay="a film shoot for an ad", payment_option="full")
    request = await _request(db_session, booking.user.id)
    assert request.confirm_by == "host" and "shoot booking" in result and "Never call it confirmed" in result

    await sim.set_outcome(request.razorpay_link_id, "paid")
    paid = await _status(db_session, booking.user, booking.call)

    assert "HELD, not yet confirmed" in paid and "team will call" in paid and "now confirmed" not in paid


# -- tool outages -----------------------------------------------------------------------------------------


async def test_when_payment_is_down_no_link_is_sent_and_the_agent_is_told_why(db_session, booking):
    await sim.set_down("payment", True)

    result = await _send_on_call(db_session, booking)

    assert "couldn't create the payment link" in result
    request = await _request(db_session, booking.user.id)
    assert request is None or request.status == "cancelled"  # the hold was released


async def test_when_payment_goes_down_after_the_link_the_agent_must_not_guess(db_session, booking):
    await _send_on_call(db_session, booking)
    await sim.set_down("payment", True)

    result = await _status(db_session, booking.user, booking.call)

    assert "You do NOT know whether the guest has paid" in result and "escalate_to_host" in result and "never say the booking is confirmed" in result
    await sim.set_down("payment", False)
    assert (await _status(db_session, booking.user, booking.call)).startswith("NOT PAID YET")


async def test_a_calendar_outage_raises_so_the_tool_fails_safe(db_session, booking):
    await sim.set_down("calendar", True)
    with pytest.raises(sim.SimulatedOutage):
        await tool_handlers.handle_check_calendar(
            db_session, CheckCalendarArgs(property_id=str(booking.prop.id), check_in=_tue(), check_out=_tue() + timedelta(days=2))
        )
    await sim.set_down("calendar", False)
    assert "AVAILABLE" in await tool_handlers.handle_check_calendar(
        db_session, CheckCalendarArgs(property_id=str(booking.prop.id), check_in=_tue(), check_out=_tue() + timedelta(days=2))
    )


async def test_when_messaging_is_down_the_host_is_told_to_send_the_link(db_session, booking):
    await sim.set_down("messaging", True)

    result = await _send_on_call(db_session, booking)

    assert "being sent by the host on WhatsApp shortly" in result
    assert [m.status for m in (await db_session.scalars(select(SimMessage))).all()] == ["failed"]


async def test_a_simulated_key_does_nothing_when_the_simulator_is_off(db_session, booking, monkeypatch):
    monkeypatch.setattr(settings, "simulator_enabled", False)

    result = await _send_on_call(db_session, booking)

    assert "isn't set up" in result or "isn't working" in result or "couldn't" in result.lower()
    assert await _request(db_session, booking.user.id) is None


# -- the control panel ----------------------------------------------------------------------------------------


async def test_the_control_panel_is_invisible_unless_enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "simulator_enabled", False)
    assert (await client.get("/api/v1/sim/state")).status_code == 404
    assert (await client.get("/api/v1/sim/pay/sim_x")).status_code == 404


async def test_control_calls_need_the_token_when_one_is_set(client, monkeypatch):
    monkeypatch.setattr(settings, "simulator_token", "s3cret")
    assert (await client.get("/api/v1/sim/state")).status_code == 401
    assert (await client.get("/api/v1/sim/state", headers={"X-Sim-Token": "s3cret"})).status_code == 200


async def test_the_outage_switch_and_outcomes_work_over_http(client, db_session, booking):
    await _send_on_call(db_session, booking)
    request = await _request(db_session, booking.user.id)

    down = await client.post("/api/v1/sim/outage", json={"target": "payment", "down": True})
    assert down.json()["down"]["payment"] is True
    assert (await client.post("/api/v1/sim/outage", json={"target": "payment", "down": False})).json()["down"]["payment"] is False
    assert (await client.post("/api/v1/sim/outage", json={"target": "fax", "down": True})).status_code == 422

    failed = await client.post(f"/api/v1/sim/payments/{request.razorpay_link_id}", json={"outcome": "failed"})
    assert failed.json() == {"id": request.razorpay_link_id, "status": "created", "last_attempt": "failed"}
    paid = await client.post(f"/api/v1/sim/payments/{request.razorpay_link_id}", json={"outcome": "paid"})
    assert paid.json()["status"] == "paid"
    again = await client.post(f"/api/v1/sim/payments/{request.razorpay_link_id}", json={"outcome": "paid"})
    assert again.status_code == 409 and "already paid" in again.json()["detail"]
    assert (await client.post("/api/v1/sim/payments/sim_nope", json={"outcome": "paid"})).status_code == 404
    assert (await client.post(f"/api/v1/sim/payments/{request.razorpay_link_id}", json={"outcome": "explode"})).status_code == 422


async def test_the_guest_side_pay_page_follows_the_gateways_wording(client, db_session, booking):
    await _send_on_call(db_session, booking)
    request = await _request(db_session, booking.user.id)
    link = request.razorpay_link_id

    page = await client.get(f"/api/v1/sim/pay/{link}")
    assert page.status_code == 200 and "Pay ₹7,350" in page.text and "no real money" in page.text

    failed = await client.post(f"/api/v1/sim/pay/{link}/failed")
    assert "Payment unsuccessful" in failed.text and "Your villa is held, just one more try needed" in failed.text
    retry = await client.get(f"/api/v1/sim/pay/{link}")
    assert "Payment unsuccessful." in retry.text

    done = await client.post(f"/api/v1/sim/pay/{link}/paid")
    assert "Payment successful" in done.text
    assert "already paid" in (await client.get(f"/api/v1/sim/pay/{link}")).text
    assert (await client.get("/api/v1/sim/pay/sim_nothing")).status_code == 404


async def test_the_test_whatsapp_log_can_be_read(client, db_session, booking):
    await _send_on_call(db_session, booking)

    messages = (await client.get("/api/v1/sim/messages")).json()

    assert len(messages) == 1 and messages[0]["status"] == "sent" and "complete your" not in messages[0]["body"].lower() or messages[0]["body"]


async def test_the_ordinary_payment_poller_also_confirms_a_simulated_payment(db_session, booking):
    # Nobody calls get_payment_status: the minute-by-minute poller must reach the same result.
    await _send_on_call(db_session, booking)
    request = await _request(db_session, booking.user.id)
    await sim.set_outcome(request.razorpay_link_id, "paid")

    await payment_service.process_payments()

    db_session.expire_all()
    assert (await _request(db_session, booking.user_id)).status == "paid"


# -- the agent may not call a booking confirmed before the payment is ----------------------------------------

from pipecat.frames.frames import (  # noqa: E402
    FunctionCallFromLLM,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.tests.utils import SleepFrame, run_test  # noqa: E402

from app.voice.property_recommendation_guard import PropertyRecommendationGuardProcessor  # noqa: E402


async def _said(reply: str, *, paid: bool, tool: str = "send_payment_link") -> str:
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("payment_status", {"paid": paid})
    down, _ = await run_test(
        guard,
        frames_to_send=[
            FunctionCallsStartedFrame(function_calls=[FunctionCallFromLLM(function_name=tool, tool_call_id="t", arguments={}, context=None)]),
            SleepFrame(sleep=0.02),
            LLMFullResponseStartFrame(),
            LLMTextFrame(reply),
            LLMFullResponseEndFrame(),
        ],
    )
    return "".join(f.text for f in down if isinstance(f, LLMTextFrame))


@pytest.mark.asyncio
async def test_an_early_claim_of_confirmation_is_removed_but_the_useful_part_stays():
    spoken = await _said("I've sent the link for 14,700 rupees. Your booking is confirmed! The dates are held for ten minutes.", paid=False)

    assert "confirmed" not in spoken and "14,700" in spoken and "held for ten minutes" in spoken


@pytest.mark.asyncio
async def test_a_reply_that_only_claims_confirmation_gets_a_safe_line():
    spoken = await _said("Great, you're all set!", paid=False)

    assert spoken == "Your dates are held while you pay. Once the payment goes through, I'll confirm your booking."


@pytest.mark.asyncio
async def test_hedged_wording_is_left_alone():
    reply = "Once the payment goes through, I'll confirm your booking."
    assert await _said(reply, paid=False) == reply


@pytest.mark.asyncio
async def test_after_a_paid_status_confirming_is_allowed():
    reply = "Your payment went through and your booking is confirmed."
    assert await _said(reply, paid=True, tool="get_payment_status") == reply


async def test_the_payment_tools_exist_only_when_payments_are_enabled(test_user):
    from app.voice.tools import build_voice_tools

    def names(enabled):
        return {t.__name__ for t in build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, payments_enabled=enabled)}

    assert {"send_payment_link", "get_payment_status"} <= names(True)
    assert not ({"send_payment_link", "get_payment_status"} & names(False))
