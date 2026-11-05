"""The agent's side of payments: the send_payment_link tool as the live voice
pipeline calls it, and what check_calendar says when dates are on hold."""

import json
import uuid
from datetime import date, timedelta

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.config import settings
from app.database import AsyncSessionLocal
from app.integrations import twilio_client
from app.models.call_session import CallSession
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.payment import HoldWaiter, PaymentRequest
from app.models.ticket import Ticket
from app.models.user import User
from app.prompts.system_prompt import build_lead_system_prompt, build_system_prompt
from app.services import billing_service, payment_service
from app.voice.conversation_state import ConversationState
from app.voice.tools import build_voice_tools

API = "https://api.razorpay.com/v1"
CHECK_IN = date.today() + timedelta(days=30)
CHECK_OUT = CHECK_IN + timedelta(days=2)


class _Params:
    def __init__(self):
        self.envelope = None
        self.result = None

    async def result_callback(self, result, properties=None):
        self.envelope = result
        self.result = result.get("result", result.get("message")) if isinstance(result, dict) else result


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(settings, "payment_encryption_key", Fernet.generate_key().decode())
    payment_service._error_alerts = payment_service.Throttle(timedelta(hours=6))


@pytest.fixture
def whatsapp(monkeypatch):
    sent: list[tuple[str, str]] = []

    async def fake(to_phone, body, timeout=15.0):
        sent.append((to_phone, body))
        return True

    async def fake_tracked(to_phone, body, timeout=15.0):
        sent.append((to_phone, body))
        return f"SM{len(sent):04d}"

    monkeypatch.setattr(twilio_client, "send_whatsapp_best_effort", fake)
    monkeypatch.setattr(twilio_client, "send_whatsapp_tracked", fake_tracked)
    return sent


@pytest.fixture
def razorpay():
    links: dict[str, dict] = {}

    def create(request):
        body = json.loads(request.content)
        link_id = f"plink_{len(links) + 1:04d}"
        links[link_id] = {"id": link_id, "short_url": f"https://rzp.io/i/{link_id}", "status": "created", "amount": body["amount"]}
        return httpx.Response(200, json=links[link_id])

    with respx.mock(assert_all_called=False) as router:
        router.post(f"{API}/payment_links").mock(side_effect=create)
        router.get(f"{API}/payment_links").mock(return_value=httpx.Response(200, json={}))
        router.links = links
        yield router


async def _connect(user_id) -> None:
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        await billing_service.connect_razorpay(db, user, "rzp_test_AbCdEf123456", "s3cretS3cretS3cret")


def _tools(state, user_id, session_id, property_id=None, payments=True):
    return {
        t.__name__: t
        for t in build_voice_tools(
            call_session_id=session_id, property_id=property_id, host_user_id=user_id, conversation_state=state, payments_enabled=payments
        )
    }


def _ready(state):
    state.set_slot("guest_name", "Asha Rao")
    state.set_slot("phone", "9999999999")


async def _quote(tools, state, test_property):
    params = _Params()
    await tools["get_pricing"](params, property_id=str(test_property.id), check_in=CHECK_IN.isoformat(), check_out=CHECK_OUT.isoformat(), num_guests=2)
    assert state.quoted_price and state.quoted_price["total"] > 0, params.result
    return state.quoted_price["total"]


# ── the tool ──────────────────────────────────────────────────────────────


async def test_the_tool_only_exists_for_hosts_with_payments_connected(test_user, test_call_session):
    assert "send_payment_link" not in _tools(ConversationState(), test_user.id, test_call_session.id, payments=False)
    assert "send_payment_link" in _tools(ConversationState(), test_user.id, test_call_session.id, payments=True)


async def test_the_link_charges_exactly_the_price_mira_quoted(test_user, test_property, test_call_session, razorpay, whatsapp):
    await _connect(test_user.id)
    state = ConversationState()
    _ready(state)
    tools = _tools(state, test_user.id, test_call_session.id, test_property.id)
    quoted = await _quote(tools, state, test_property)

    params = _Params()
    await tools["send_payment_link"](params, property_id=str(test_property.id), guest_name="Asha Rao")

    assert params.envelope["status"] == "ok", params.result
    assert razorpay.links["plink_0001"]["amount"] == round(quoted * 100)
    assert payment_service.rupees(quoted) in params.result and "60 minutes" in params.result
    assert "rzp.io" not in params.result  # the link goes to WhatsApp, not into the conversation
    assert whatsapp and "rzp.io/i/plink_0001" in whatsapp[0][1] and whatsapp[0][0] == "9999999999"
    async with AsyncSessionLocal() as db:
        request = await db.scalar(select(PaymentRequest))
        assert (request.check_in, request.check_out, request.status) == (CHECK_IN, CHECK_OUT, "active")
        assert request.lead_id is not None  # the opportunity is on file too
        assert await db.scalar(select(func.count()).select_from(Lead).where(Lead.user_id == test_user.id)) == 1


async def test_the_model_cannot_choose_the_amount_or_dates(test_user, test_property, test_call_session, razorpay, whatsapp):
    await _connect(test_user.id)
    state = ConversationState()
    _ready(state)
    tools = _tools(state, test_user.id, test_call_session.id, test_property.id)
    quoted = await _quote(tools, state, test_property)

    params = _Params()
    await tools["send_payment_link"](params, property_id=str(test_property.id), amount=1, total=1, check_in="2031-01-01")  # invented arguments

    assert params.envelope["status"] == "invalid_tool_arguments"  # the tool has no amount/date parameters to set
    assert razorpay.links == {} and whatsapp == []

    ok = _Params()  # and a proper call afterwards charges the quoted price, nothing else
    await tools["send_payment_link"](ok, property_id=str(test_property.id))
    assert razorpay.links["plink_0001"]["amount"] == round(quoted * 100)


@pytest.mark.parametrize(
    "quote",
    [
        None,
        {"property_name": "Test Villa", "check_in": None, "check_out": None, "total": 9000, "nights": 3, "is_estimate": True},
        # exact dates, but flagged an estimate: still not something to charge
        {"property_name": "Test Villa", "check_in": CHECK_IN.isoformat(), "check_out": CHECK_OUT.isoformat(), "total": 9000, "nights": 2, "is_estimate": True},
        {"property_name": "Some Other Villa", "check_in": CHECK_IN.isoformat(), "check_out": CHECK_OUT.isoformat(), "total": 9000, "nights": 2, "is_estimate": False},
        {"property_name": "Test Villa", "check_in": CHECK_IN.isoformat(), "check_out": CHECK_OUT.isoformat(), "total": 0, "nights": 2, "is_estimate": False},
    ],
)
async def test_no_link_without_a_real_quote_for_exact_dates_at_this_property(test_user, test_property, test_call_session, razorpay, whatsapp, quote):
    await _connect(test_user.id)
    state = ConversationState()
    state.quoted_price = quote
    params = _Params()

    await _tools(state, test_user.id, test_call_session.id, test_property.id)["send_payment_link"](params, property_id=str(test_property.id))

    assert params.envelope["status"] == "business_error" and "price" in params.result.lower()
    assert whatsapp == [] and razorpay.links == {}  # nothing was sent and no link was created


async def test_no_link_when_the_host_has_not_connected_razorpay(test_user, test_property, test_call_session, razorpay, whatsapp):
    state = ConversationState()
    state.quoted_price = {"property_name": test_property.name, "check_in": CHECK_IN.isoformat(), "check_out": CHECK_OUT.isoformat(), "total": 9000, "nights": 2, "is_estimate": False}
    params = _Params()
    await _tools(state, test_user.id, test_call_session.id, test_property.id)["send_payment_link"](params, property_id=str(test_property.id))
    assert params.envelope["status"] == "business_error" and "isn't set up" in params.result
    assert whatsapp == []


async def test_a_property_that_is_not_the_hosts_is_refused(test_user, test_property, test_call_session, razorpay, whatsapp, db_session):
    await _connect(test_user.id)
    other = User(email=f"o-{uuid.uuid4().hex[:6]}@example.com", clerk_user_id=f"user_{uuid.uuid4().hex[:12]}")
    db_session.add(other)
    await db_session.commit()
    await _connect(other.id)  # fully set up for payments, so only ownership can stop this
    state = ConversationState()
    state.quoted_price = {"property_name": test_property.name, "check_in": CHECK_IN.isoformat(), "check_out": CHECK_OUT.isoformat(), "total": 9000, "nights": 2, "is_estimate": False}
    params = _Params()
    await _tools(state, other.id, test_call_session.id, test_property.id)["send_payment_link"](params, property_id=str(test_property.id))
    assert params.envelope["status"] == "business_error"
    assert whatsapp == []


async def test_a_guest_who_gives_a_different_number_gets_the_link_there(test_user, test_property, test_call_session, razorpay, whatsapp):
    await _connect(test_user.id)
    state = ConversationState()
    _ready(state)
    tools = _tools(state, test_user.id, test_call_session.id, test_property.id)
    await _quote(tools, state, test_property)

    await tools["send_payment_link"](_Params(), property_id=str(test_property.id), phone="+91 98765 43210")

    assert whatsapp[0][0] == "9876543210"


# ── dates on hold ─────────────────────────────────────────────────────────


async def _second_caller(db, test_user, test_property, number="+919822222222") -> CallSession:
    session = CallSession(exotel_call_id=f"call-{uuid.uuid4().hex[:8]}", user_id=test_user.id, property_id=test_property.id, caller_number=number, status="in_progress")
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def _hold(test_user, test_property, test_call_session, razorpay, whatsapp):
    await _connect(test_user.id)
    state = ConversationState()
    _ready(state)
    tools = _tools(state, test_user.id, test_call_session.id, test_property.id)
    await _quote(tools, state, test_property)
    params = _Params()
    await tools["send_payment_link"](params, property_id=str(test_property.id))
    assert params.envelope["status"] == "ok", params.result


async def _check(user_id, session_id, property_id, ci=CHECK_IN, co=CHECK_OUT):
    params = _Params()
    await _tools(ConversationState(), user_id, session_id, property_id)["check_calendar"](
        params, property_id=str(property_id), check_in=ci.isoformat(), check_out=co.isoformat()
    )
    return params


async def test_a_second_guest_is_told_the_dates_are_on_hold_and_the_host_is_alerted(
    test_user, test_property, test_call_session, razorpay, whatsapp, db_session
):
    test_user.phone = "+919800000001"  # where the host's alerts go
    await db_session.commit()
    await _hold(test_user, test_property, test_call_session, razorpay, whatsapp)
    second = await _second_caller(db_session, test_user, test_property)
    whatsapp.clear()

    params = await _check(test_user.id, second.id, test_property.id)

    text = params.result
    assert "ON HOLD" in text and "WhatsApp if it is released" in text and "minutes" in text
    assert "Asha" not in text and "9999999999" not in text  # never the other guest's identity
    assert "do not promise the dates" in text.lower()
    assert await db_session.scalar(select(func.count()).select_from(HoldWaiter)) == 1
    note = await db_session.scalar(select(Notification).where(Notification.channel == "escalation"))
    assert note.urgency == "high" and "on hold" in note.message
    ticket = await db_session.scalar(select(Ticket).where(Ticket.kind == "payment"))
    assert ticket.urgency == "high"
    assert [(p, b) for p, b in whatsapp if p == "+919800000001" and "on hold" in b]  # the host was messaged


async def test_asking_again_does_not_re_alert_the_host(test_user, test_property, test_call_session, razorpay, whatsapp, db_session):
    await _hold(test_user, test_property, test_call_session, razorpay, whatsapp)
    second = await _second_caller(db_session, test_user, test_property)

    for _ in range(3):
        await _check(test_user.id, second.id, test_property.id)

    assert await db_session.scalar(select(func.count()).select_from(Notification).where(Notification.channel == "escalation")) == 1
    assert await db_session.scalar(select(func.count()).select_from(Ticket).where(Ticket.kind == "payment")) == 1
    assert await db_session.scalar(select(func.count()).select_from(HoldWaiter)) == 1


async def test_the_holding_guest_is_told_it_is_their_own_hold(test_user, test_property, test_call_session, razorpay, whatsapp, db_session):
    await _hold(test_user, test_property, test_call_session, razorpay, whatsapp)

    params = await _check(test_user.id, test_call_session.id, test_property.id)

    assert "already ON HOLD for this guest" in params.result and "payment link was sent" in params.result
    assert await db_session.scalar(select(func.count()).select_from(HoldWaiter)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Notification).where(Notification.channel == "escalation")) == 0


async def test_a_caller_without_a_number_is_told_not_to_expect_a_message(test_user, test_property, test_call_session, razorpay, whatsapp, db_session):
    await _hold(test_user, test_property, test_call_session, razorpay, whatsapp)
    anonymous = await _second_caller(db_session, test_user, test_property, number="browser-test")

    params = await _check(test_user.id, anonymous.id, test_property.id)

    assert "ON HOLD" in params.result and "do not promise a message" in params.result
    assert await db_session.scalar(select(func.count()).select_from(HoldWaiter)) == 0


async def test_a_paid_booking_is_simply_not_available(test_user, test_property, test_call_session, razorpay, whatsapp, db_session):
    await _hold(test_user, test_property, test_call_session, razorpay, whatsapp)
    async with AsyncSessionLocal() as db:
        request = await db.scalar(select(PaymentRequest))
        await payment_service._mark_paid(db, request.id, "pay_1")
    second = await _second_caller(db_session, test_user, test_property)

    params = await _check(test_user.id, second.id, test_property.id)

    assert "NOT available" in params.result and "HOLD" not in params.result
    assert await db_session.scalar(select(func.count()).select_from(HoldWaiter)) == 0


async def test_dates_that_are_still_free_are_unaffected(test_user, test_property, test_call_session, razorpay, whatsapp, db_session):
    await _hold(test_user, test_property, test_call_session, razorpay, whatsapp)
    second = await _second_caller(db_session, test_user, test_property)
    later = CHECK_OUT + timedelta(days=10)

    params = await _check(test_user.id, second.id, test_property.id, later, later + timedelta(days=2))

    assert "AVAILABLE" in params.result


async def test_a_guest_who_had_the_link_sent_to_another_number_is_still_the_holder(test_user, test_property, test_call_session, razorpay, whatsapp, db_session):
    await _connect(test_user.id)
    state = ConversationState()
    _ready(state)
    tools = _tools(state, test_user.id, test_call_session.id, test_property.id)
    await _quote(tools, state, test_property)
    sent = _Params()
    await tools["send_payment_link"](sent, property_id=str(test_property.id), phone="98765 43210")  # a different number from the one calling
    assert sent.envelope["status"] == "ok", sent.result

    params = await _check(test_user.id, test_call_session.id, test_property.id)

    assert "already ON HOLD for this guest" in params.result  # not a rival, even though the numbers differ
    assert await db_session.scalar(select(func.count()).select_from(HoldWaiter)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Notification).where(Notification.channel == "escalation")) == 0
    assert await db_session.scalar(select(func.count()).select_from(Ticket).where(Ticket.kind == "payment")) == 0


async def test_the_holder_asking_for_overlapping_other_dates_is_not_treated_as_a_rival(test_user, test_property, test_call_session, razorpay, whatsapp, db_session):
    await _hold(test_user, test_property, test_call_session, razorpay, whatsapp)
    state = ConversationState()
    _ready(state)
    tools = _tools(state, test_user.id, test_call_session.id, test_property.id)
    shifted_in, shifted_out = CHECK_IN + timedelta(days=1), CHECK_OUT + timedelta(days=1)
    await tools["get_pricing"](_Params(), property_id=str(test_property.id), check_in=shifted_in.isoformat(), check_out=shifted_out.isoformat(), num_guests=2)

    params = _Params()
    await tools["send_payment_link"](params, property_id=str(test_property.id))

    assert params.envelope["status"] == "business_error" and "already has" in params.result
    assert await db_session.scalar(select(func.count()).select_from(HoldWaiter)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Ticket).where(Ticket.kind == "payment")) == 0
    assert await db_session.scalar(select(func.count()).select_from(PaymentRequest)) == 1


# ── prompts ───────────────────────────────────────────────────────────────


async def test_the_prompts_mention_online_payment_only_when_the_call_has_the_tool(test_user, test_property):
    off_lead = build_lead_system_prompt(test_user, [test_property])
    off_support = build_system_prompt(test_property, None, test_user)
    assert "send_payment_link" not in off_lead and "send_payment_link" not in off_support

    for prompt in (
        build_lead_system_prompt(test_user, [test_property], payments_enabled=True),
        build_system_prompt(test_property, None, test_user, payments_enabled=True),
    ):
        assert "send_payment_link" in prompt and "Never ask for card, UPI" in prompt
        assert "₹" not in prompt.split("Taking a booking payment")[1].split("\n\n")[0]  # no amounts or durations baked into the prompt


# ── the "Online payments" capability ─────────────────────────────────────


async def _capability(db, user, **changes):
    from app.schemas.capability import CapabilityUpdate
    from app.services import capability_service

    if changes:
        await capability_service.update_capability(db, user, "online_payments", CapabilityUpdate(**changes))
    caps = await capability_service.host_capabilities(db, user)
    return next(c for c in caps.capabilities if c.id == "online_payments")


async def test_online_payments_needs_setup_until_razorpay_is_connected(test_user, db_session, razorpay):
    before = await _capability(db_session, test_user)
    assert before.enabled is True and before.state == "needs_setup"
    assert [r.id for r in before.requirements if not r.met] == ["razorpay_connected"]

    await _connect(test_user.id)
    await db_session.refresh(test_user)
    after = await _capability(db_session, test_user)
    assert after.state == "ready" and after.available is True


async def test_online_payments_is_unavailable_when_the_server_cannot_store_keys_safely(test_user, db_session, monkeypatch):
    monkeypatch.setattr(settings, "payment_encryption_key", None)
    cap = await _capability(db_session, test_user)
    assert cap.available is False and cap.state == "unavailable"
    assert [(i.id, i.connected) for i in cap.integrations] == [("payment_encryption", False)]


async def test_switching_the_capability_off_removes_the_tool_and_the_prompt_text(test_user, test_property, db_session, razorpay):
    await _connect(test_user.id)
    await db_session.refresh(test_user)
    assert await payment_service.payments_enabled_for(test_user) is True

    await _capability(db_session, test_user, enabled=False)

    assert await payment_service.payments_enabled_for(test_user) is False  # the call gets neither the tool nor the prompt section
    assert (await _capability(db_session, test_user)).enabled is False


async def test_a_host_without_razorpay_never_gets_the_tool_even_with_the_capability_on(test_user, db_session):
    assert (await _capability(db_session, test_user)).enabled is True
    assert await payment_service.payments_enabled_for(test_user) is False
    assert await payment_service.payments_enabled_for(None) is False


async def test_the_billing_screen_says_when_the_capability_is_off(client, test_user, db_session, razorpay):
    from tests.conftest import auth_headers_for

    await _connect(test_user.id)
    headers = auth_headers_for(test_user)
    assert (await client.get("/api/v1/billing", headers=headers)).json()["capability_enabled"] is True

    await db_session.refresh(test_user)
    await _capability(db_session, test_user, enabled=False)
    body = (await client.get("/api/v1/billing", headers=headers)).json()
    assert body["capability_enabled"] is False and body["razorpay"]["connected"] is True
