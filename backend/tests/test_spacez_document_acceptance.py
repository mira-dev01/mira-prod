"""The Spacez requirements document's own "first test", end to end through the REAL tool wrappers (the same
functions the voice agent calls), with the document's villas, rates and calendar, on the test services.

  A guest calls about Casa Blu for the night of 5 December, for 8 adults. It's booked, so the agent offers another
  villa. The guest moves to Deccan Escape for 8 to 10 December, two weeknights at the sample Rs 7,000, for a birthday.
  Total reservation Rs 14,000, GST 5% Rs 700, total Rs 14,700, or Rs 7,350 now on split payment. The agent takes the
  guest's details, creates the booking, sends the link, and confirms once the link shows paid.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.config import settings
from tests import fake_providers as sim
from tests.fake_providers import fake_providers_on  # noqa: F401  (autouse: fake payment provider + WhatsApp)
from app.models.booking import Booking
from app.models.payment import PaymentRequest
from app.models.property import Property
from app.models.user import User
from app.schemas.agent_config import AgentConfig, BookingConfig, PaymentConfig
from app.voice.conversation_state import ConversationState
from app.voice.property_recommendation_guard import PropertyRecommendationGuardProcessor
from app.voice.tools import build_voice_tools

pytestmark = pytest.mark.skipif(date.today() >= date(2026, 12, 1), reason="the document's calendar is December 2026")


class _Params:
    def __init__(self):
        self.envelope = None
        self.tool_call_id = "tc"
        self.context = None

    async def result_callback(self, result, properties=None):
        self.envelope = result

    @property
    def text(self) -> str:
        return self.envelope["result"] if "result" in self.envelope else self.envelope.get("message", "")


@pytest.fixture
async def world(db_session, test_user, test_call_session, monkeypatch):
    user = await db_session.get(User, test_user.id)
    user.billing_config = sim.billing_config()
    user.agent_config = AgentConfig(payment=PaymentConfig(hold_minutes=10), booking=BookingConfig(confirm_by="payment")).model_dump()

    def villa(name, rate, mx, base, city, **extra):
        return Property(user_id=user.id, name=name, city=city, base_price=rate, max_guests=mx, exophone=None,
                        pricing_terms={"base_guests": base, **extra})

    casa, deccan = villa("Casa Blu", 10000, 10, 8, "Bengaluru"), villa("Deccan Escape", 7000, 12, 8, "Hyderabad")
    db_session.add_all([casa, deccan])
    await db_session.flush()
    # the document's sample calendar: Casa Blu 4 to 6 December (nights 4, 5 and 6), Deccan Escape 12 and 13
    db_session.add_all([
        Booking(property_id=casa.id, check_in=date(2026, 12, 4), check_out=date(2026, 12, 7), platform="manual", status="confirmed"),
        Booking(property_id=deccan.id, check_in=date(2026, 12, 12), check_out=date(2026, 12, 14), platform="manual", status="confirmed"),
    ])
    await db_session.commit()

    class W:
        pass

    w = W()
    w.user_id, w.casa_id, w.deccan_id, w.call_id = user.id, str(casa.id), str(deccan.id), test_call_session.id
    w.state = ConversationState()
    w.guard = PropertyRecommendationGuardProcessor()
    tools = build_voice_tools(
        call_session_id=test_call_session.id, property_id=None, host_user_id=user.id, conversation_state=w.state,
        property_recommendation_guard=w.guard, payments_enabled=True,
    )
    w.tool = {t.__name__: t for t in tools}
    return w


async def _call(w, name, **kw):
    params = _Params()
    await w.tool[name](params, **kw)
    return params


async def test_the_documents_first_test_from_enquiry_to_confirmed(world, db_session):
    w = world
    # the guest gives their name and number early (the agent saves them), as any real call does
    w.state.set_slot("guest_name", "Asha Rao")
    w.state.set_slot("phone", "9876543210")

    # 1. Casa Blu, night of 5 December: booked -> the agent is handed only verified alternatives
    casa = await _call(w, "check_calendar", property_id=w.casa_id, check_in="2026-12-05", check_out="2026-12-06", num_guests=8)
    assert "NOT available" in casa.text and "Offer ONLY these" in casa.text
    assert "2026-12-07 to 2026-12-08" in casa.text  # night of the 7th is the next free one (nights 4-6 are booked)

    # 2. the guest moves to Deccan Escape for 8 to 10 December
    free = await _call(w, "check_calendar", property_id=w.deccan_id, check_in="2026-12-08", check_out="2026-12-10", num_guests=8)
    assert "AVAILABLE" in free.text and "NOT" not in free.text

    # 3. the quote: total reservation 14,000; GST 5% is 700; total 14,700; split 7,350 now
    quote = await _call(w, "get_pricing", property_id=w.deccan_id, check_in="2026-12-08", check_out="2026-12-10", num_guests=8)
    # (the call hears the room price and the one total; the line-by-line bill goes out with the link)
    assert "₹7,000 a night" in quote.text and "₹14,700" in quote.text
    assert "bill summary" in quote.text
    assert w.state.quoted_price["total"] == 14700 and w.state.quoted_price["bill"]["split_now"] == 7350

    # 4. the booking needs details; the agent may not choose how to pay for the guest
    early = await _call(w, "send_payment_link", property_id=w.deccan_id, guest_name="Asha Rao")
    assert "ask the guest for" in early.text and "email" in early.text
    undecided = await _call(w, "send_payment_link", property_id=w.deccan_id, guest_name="Asha Rao", email="asha@example.com", purpose_of_stay="birthday")
    assert "Ask the guest how they want to pay" in undecided.text

    # 5. the link: split payment, held ten minutes
    sent = await _call(w, "send_payment_link", property_id=w.deccan_id, guest_name="Asha Rao", email="asha@example.com",
                       purpose_of_stay="birthday", payment_option="split")
    assert "₹7,350" in sent.text and "NOT confirmed yet" in sent.text
    request = await db_session.scalar(select(PaymentRequest).where(PaymentRequest.user_id == w.user_id))
    assert (request.amount_due, request.total_amount, request.kind) == (7350, 14700, "advance")
    assert request.details["purpose"] == "Birthday"

    # 6. not paid yet -> never "confirmed"
    waiting = await _call(w, "get_payment_status")
    assert waiting.text.startswith("NOT PAID YET")

    # 7. the guest's attempt fails, then they pay
    await sim.set_outcome(request.razorpay_link_id, "failed")
    failed = await _call(w, "get_payment_status")
    assert failed.text.startswith("NOT PAID") and "Payment unsuccessful" in failed.text
    await sim.set_outcome(request.razorpay_link_id, "paid")
    paid = await _call(w, "get_payment_status")
    assert paid.text.startswith("PAID") and "now confirmed" in paid.text and "stay code" in paid.text

    # 8. the booking is real, and the guest got the document's confirmation message
    db_session.expire_all()
    request = await db_session.scalar(select(PaymentRequest).where(PaymentRequest.user_id == w.user_id))
    booking = await db_session.get(Booking, request.booking_id)
    assert (booking.check_in, booking.check_out, booking.status, booking.kind) == (date(2026, 12, 8), date(2026, 12, 10), "confirmed", "reservation")
    texts = sim.message_bodies()
    confirmation = next(t for t in texts if "Your Booking is now confirmed" in t)
    assert "Check in Date: 08 Dec, 2026" in confirmation and "Check Out Date: 10 Dec, 2026" in confirmation
    assert "Stay Name: Deccan Escape" in confirmation and "Number of Guests: 8 Adults, 0 Children, 0 Pets" in confirmation
    assert "Payment Amount: ₹7,350" in confirmation and "Payment Remaining: ₹7,350" in confirmation
    assert confirmation.endswith("Regards,\nTeam Spacez\n\nwww.spacez.co")

    # 9. nobody else can take those nights now
    again = await _call(w, "check_calendar", property_id=w.deccan_id, check_in="2026-12-09", check_out="2026-12-10", num_guests=8)
    assert "NOT available" in again.text


async def test_the_document_calendar_blocks_exactly_the_nights_it_lists(world):
    async def asks(prop, a, b):
        return (await _call(world, "check_calendar", property_id=prop, check_in=a, check_out=b, num_guests=2)).text

    world.state.set_slot("guest_name", "Asha Rao")
    world.state.set_slot("phone", "9876543210")
    # Casa Blu nights 4, 5 and 6 are booked; the 3rd and the 7th are free
    assert "NOT available" in await asks(world.casa_id, "2026-12-04", "2026-12-05")
    assert "NOT available" in await asks(world.casa_id, "2026-12-06", "2026-12-07")
    assert "AVAILABLE" in await asks(world.casa_id, "2026-12-03", "2026-12-04")
    assert "AVAILABLE" in await asks(world.casa_id, "2026-12-07", "2026-12-08")
    # Deccan Escape nights 12 and 13 are booked
    assert "NOT available" in await asks(world.deccan_id, "2026-12-13", "2026-12-14")
    assert "AVAILABLE" in await asks(world.deccan_id, "2026-12-14", "2026-12-15")


async def test_the_whole_requirement_journey_through_the_real_tool_wrappers(world, db_session):
    """Weekend rate -> a short spoken quote (early check-in asked for) -> the link with the bill summary -> paid ->
    the guest rings back for a late check-out -> the team gets a ticket. The tools are the ones the agent calls."""
    from app.models.property import Property
    from app.models.ticket import Ticket

    w = world
    deccan = await db_session.get(Property, uuid.UUID(w.deccan_id))
    deccan.weekend_rate_enabled, deccan.weekend_price = True, 9000
    await db_session.commit()
    tools = build_voice_tools(
        call_session_id=w.call_id, property_id=None, host_user_id=w.user_id, conversation_state=w.state,
        property_recommendation_guard=w.guard, payments_enabled=True, caller_number="+919876543210", spacez_enabled=True,
    )
    w.tool = {t.__name__: t for t in tools}
    w.state.set_slot("guest_name", "Asha Rao")
    w.state.set_slot("phone", "9876543210")

    # Fri 18 and Sat 19 December at the weekend rate; the guest asked for an early check-in
    quote = await _call(w, "get_pricing", property_id=w.deccan_id, check_in="2026-12-18", check_out="2026-12-20", num_guests=8,
                        requested_early_checkin=True)
    assert "₹9,000 a night" in quote.text and "₹22,240" in quote.text  # 18,000 + 18% GST 3,240 + early check-in 1,000
    assert "GST at" not in quote.text and "bill summary" in quote.text
    assert w.state.quoted_price["total"] == 22240

    sent = await _call(w, "send_payment_link", property_id=w.deccan_id, guest_name="Asha Rao", email="asha@example.com",
                       purpose_of_stay="birthday", payment_option="full",
                       phone="9876543210")  # the guest gave this number for the link, so a call back from it is recognised
    assert "₹22,240" in sent.text and "together with their bill summary" in sent.text
    body = sim.message_bodies()[-1]
    for line in ("complete your Spacez booking payment here: https://rzp.test/pay/",
                 "Bill: Stay ₹18,000 (2 Friday/Saturday night(s) at ₹9,000) + GST (18%) ₹3,240 + Early check-in ₹1,000",
                 "Grand total: ₹22,240 (full payment)"):
        assert line in body, line
    assert "Lunch" not in body and "Pickup" not in body  # only what applies

    request = await db_session.scalar(select(PaymentRequest).where(PaymentRequest.user_id == w.user_id))
    await sim.set_outcome(request.razorpay_link_id, "paid")
    assert (await _call(w, "get_payment_status")).text.startswith("PAID")

    # the guest rings back (same number) and asks for a late check-out
    late = await _call(w, "request_early_late_checkout", late_checkout=True, note="around 3 pm")
    assert "₹1,000" in late.text and "let the team check whether it is possible" in late.text
    ticket = await db_session.scalar(select(Ticket).where(Ticket.user_id == w.user_id, Ticket.kind == "stay_request"))
    assert ticket is not None and ticket.status == "open"
