"""Payment links and date holds against real Postgres. Razorpay is stubbed at
the HTTP layer (respx), so the real client code runs: amounts in paise, the
request shapes, the status handling. WhatsApp sends are captured."""

import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from sqlalchemy import func, select, update

from app.config import settings
from app.database import AsyncSessionLocal
from app.integrations import razorpay_client, twilio_client
from app.models.booking import Booking
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.payment import HoldWaiter, PaymentRequest
from app.models.property import Property
from app.models.ticket import Ticket
from app.models.user import User
from app.services import billing_service, calendar_service, payment_service
from app.services.billing_service import BillingError
from app.services.payment_service import DatesOnHold, DatesUnavailable, PaymentError

API = "https://api.razorpay.com/v1"
KEY_ID, KEY_SECRET = "rzp_test_AbCdEf123456", "s3cretS3cretS3cret"
CHECK_IN = date.today() + timedelta(days=30)
CHECK_OUT = CHECK_IN + timedelta(days=3)


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _encryption(monkeypatch):
    monkeypatch.setattr(settings, "payment_encryption_key", Fernet.generate_key().decode())
    payment_service._error_alerts = payment_service.Throttle(timedelta(hours=6))


@pytest.fixture
def whatsapp(monkeypatch):
    """Captures every WhatsApp send. `outcome["accepted"]` makes Twilio refuse;
    `outcome["delivery"]` is what Twilio later says became of a tracked message."""
    sent: list[tuple[str, str]] = []
    outcome = {"accepted": True, "delivery": ("delivered", None)}

    async def fake(to_phone, body, timeout=15.0):
        sent.append((to_phone, body))
        return outcome["accepted"]

    async def fake_tracked(to_phone, body, timeout=15.0):
        sent.append((to_phone, body))
        return f"SM{len(sent):04d}" if outcome["accepted"] else None

    async def fake_status(message_sid, timeout=10.0):
        result = outcome["delivery"]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(twilio_client, "send_whatsapp_best_effort", fake)
    monkeypatch.setattr(twilio_client, "send_whatsapp_tracked", fake_tracked)
    monkeypatch.setattr(twilio_client, "fetch_message_status", fake_status)
    fake.outcome, fake.sent = outcome, sent
    return fake


@pytest.fixture
def razorpay():
    """Razorpay's API. `links` is what it 'remembers'; tests flip statuses."""
    links: dict[str, dict] = {}
    calls: list[tuple[str, str]] = []

    def create(request: httpx.Request):
        body = json.loads(request.content)
        calls.append(("create", body["reference_id"]))
        link_id = f"plink_{len(links) + 1:04d}"
        links[link_id] = {"id": link_id, "short_url": f"https://rzp.io/i/{link_id}", "status": "created", "amount": body["amount"],
                          "payments": None, "_body": body}
        return httpx.Response(200, json=links[link_id])

    def fetch(request: httpx.Request):
        link_id = request.url.path.rsplit("/", 1)[-1]
        calls.append(("fetch", link_id))
        return httpx.Response(200, json=links[link_id]) if link_id in links else httpx.Response(404, json={"error": {"description": "not found"}})

    def cancel(request: httpx.Request):
        link_id = request.url.path.split("/")[-2]
        calls.append(("cancel", link_id))
        link = links[link_id]
        if link["status"] != "created":
            return httpx.Response(400, json={"error": {"description": "cannot cancel"}})
        link["status"] = "cancelled"
        return httpx.Response(200, json=link)

    with respx.mock(assert_all_called=False) as router:
        router.post(f"{API}/payment_links").mock(side_effect=create)
        router.get(url__regex=rf"{API}/payment_links/plink_\w+").mock(side_effect=fetch)
        router.post(url__regex=rf"{API}/payment_links/plink_\w+/cancel").mock(side_effect=cancel)
        router.get(f"{API}/payment_links").mock(return_value=httpx.Response(200, json={"payment_links": []}))
        router.links, router.seen = links, calls
        yield router


def pay(router, link_id: str, payment_id: str = "pay_0001") -> None:
    router.links[link_id]["status"] = "paid"
    router.links[link_id]["payments"] = [{"payment_id": payment_id, "status": "captured"}]


async def _host(db, name="Host", config: dict | None = None, connected=True) -> User:
    user = User(
        email=f"{name.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        clerk_user_id=f"user_{uuid.uuid4().hex[:16]}",
        name=name,
        phone="+919800000001",
        agent_config=config,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    if connected:
        await billing_service.connect_razorpay(db, user, KEY_ID, KEY_SECRET)
    return user


async def _property(db, user, name="Sea View") -> Property:
    prop = Property(user_id=user.id, name=name, city="Goa", base_price=4000, exophone=f"+9180{uuid.uuid4().int % 10**8:08d}")
    db.add(prop)
    await db.commit()
    await db.refresh(prop)
    return prop


async def _request(db, user, prop, *, guest="Asha Rao", phone="+919811111111", total=12000, ci=CHECK_IN, co=CHECK_OUT, **kw) -> PaymentRequest:
    return await payment_service.create_payment_request(
        db, user, prop, check_in=ci, check_out=co, total=total, guest_name=guest, guest_phone=phone, **kw
    )


async def _fresh(request_id) -> PaymentRequest:
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(PaymentRequest).where(PaymentRequest.id == request_id))


async def _bookings(prop_id) -> list[Booking]:
    async with AsyncSessionLocal() as db:
        return list((await db.scalars(select(Booking).where(Booking.property_id == prop_id))).all())


async def _available(prop_id, ci=CHECK_IN, co=CHECK_OUT) -> bool:
    async with AsyncSessionLocal() as db:
        return await calendar_service.is_available(db, prop_id, ci, co)


PAID_CONFIRMS = {"version": 1, "booking": {"confirm_by": "payment"}}


# ── connecting Razorpay ───────────────────────────────────────────────────


async def test_the_secret_is_encrypted_and_never_returned(db_session, razorpay):
    host = await _host(db_session, connected=False)
    await billing_service.connect_razorpay(db_session, host, KEY_ID, KEY_SECRET)

    stored = json.dumps(host.billing_config)
    assert KEY_SECRET not in stored and "secret_enc" in stored
    assert KEY_SECRET not in json.dumps(billing_service.public_view(host))
    assert billing_service.get_credentials(host) == (KEY_ID, KEY_SECRET)
    assert billing_service.public_view(host)["razorpay"] == {"connected": True, "key_id": KEY_ID, "mode": "test", "verified_at": host.billing_config["razorpay"]["verified_at"]}


async def test_keys_razorpay_refuses_are_not_saved(db_session, razorpay):
    razorpay.get(f"{API}/payment_links").mock(return_value=httpx.Response(401, json={"error": {"description": "Authentication failed"}}))
    host = await _host(db_session, connected=False)
    with pytest.raises(BillingError, match="didn't accept"):
        await billing_service.connect_razorpay(db_session, host, KEY_ID, KEY_SECRET)
    assert not billing_service.is_connected(host)


@pytest.mark.parametrize("key_id,secret", [("not-a-key", KEY_SECRET), ("rzp_test_AbCdEf123456", "short")])
async def test_obviously_wrong_keys_are_refused_before_calling_razorpay(db_session, razorpay, key_id, secret):
    host = await _host(db_session, connected=False)
    with pytest.raises(BillingError):
        await billing_service.connect_razorpay(db_session, host, key_id, secret)
    assert razorpay.seen == []


async def test_without_a_server_encryption_key_nothing_is_stored(db_session, razorpay, monkeypatch):
    monkeypatch.setattr(settings, "payment_encryption_key", None)
    host = await _host(db_session, connected=False)
    with pytest.raises(BillingError, match="aren't enabled"):
        await billing_service.connect_razorpay(db_session, host, KEY_ID, KEY_SECRET)
    assert host.billing_config is None
    assert billing_service.public_view(host)["encryption_ready"] is False


async def test_cannot_disconnect_while_a_link_is_open(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    request = await _request(db_session, host, prop)
    with pytest.raises(BillingError, match="still open"):
        await billing_service.disconnect_razorpay(db_session, host)

    await payment_service.cancel_request(db_session, host, request.id)
    await billing_service.disconnect_razorpay(db_session, host)
    assert not billing_service.is_connected(host)


# ── message wording ───────────────────────────────────────────────────────


async def test_an_edited_message_is_only_a_draft_until_approved(db_session, razorpay):
    host = await _host(db_session)
    default = billing_service.template_text(host, "payment_request")

    await billing_service.save_template_draft(db_session, host, "payment_request", "Hello {guest_name}, pay {amount} at {link}")
    assert billing_service.template_text(host, "payment_request") == default  # guests still get the old wording
    assert billing_service.public_view(host)["templates"]["payment_request"]["draft"].startswith("Hello")

    await billing_service.approve_template(db_session, host, "payment_request")
    assert billing_service.template_text(host, "payment_request") == "Hello {guest_name}, pay {amount} at {link}"

    await billing_service.reset_template(db_session, host, "payment_request")
    assert billing_service.template_text(host, "payment_request") == default


@pytest.mark.parametrize("text", ["pay now", "Pay {amount} {nope} {link}", "Pay {link} {", "", "x" * 2000 + "{link}"])
async def test_unusable_messages_are_refused(db_session, razorpay, text):
    host = await _host(db_session)
    with pytest.raises(BillingError):
        await billing_service.save_template_draft(db_session, host, "payment_request", text)


# ── creating a link and holding the dates ─────────────────────────────────


async def test_a_link_is_created_for_the_right_amount_and_the_dates_are_held(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)

    request = await _request(db_session, host, prop, total=12345.50)

    body = razorpay.links["plink_0001"]["_body"]
    assert body["amount"] == 1234550  # paise, not rupees, no float drift
    assert body["currency"] == "INR" and body["reference_id"] == request.id.hex
    assert body["notify"] == {"sms": False, "email": False}  # Mira sends it; Razorpay must not send a second copy
    assert request.status == "active" and request.short_url == "https://rzp.io/i/plink_0001"
    assert request.hold_expires_at > datetime.now(timezone.utc) + timedelta(minutes=59)
    assert await _available(prop.id) is False  # held for everyone else
    hold_rows = [b for b in await _bookings(prop.id) if b.platform == "mira_hold"]
    assert len(hold_rows) == 1 and hold_rows[0].kind == "blocked" and hold_rows[0].source_uid is None

    phone, text = whatsapp.sent[0]
    assert phone == "+919811111111" and "https://rzp.io/i/plink_0001" in text and "₹12,345.50" in text and "60 minutes" in text
    assert request.link_sent_at is not None


async def test_asking_twice_returns_the_same_link_and_hold(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    first = await _request(db_session, host, prop)
    second = await _request(db_session, host, prop)
    assert second.id == first.id
    assert [c for c in razorpay.seen if c[0] == "create"] == [("create", first.id.hex)]
    assert len(whatsapp.sent) == 1


async def test_split_payment_charges_the_advance_and_says_when_the_rest_is_due(db_session, razorpay, whatsapp):
    config = {"version": 1, "payment": {"kind": "split", "split_percent": 30, "balance_due_days_before_checkin": 7, "hold_minutes": 90}}
    host = await _host(db_session, config=config)
    prop = await _property(db_session, host)

    request = await _request(db_session, host, prop, total=10000)

    assert (request.amount_due, request.total_amount, request.kind) == (3000, 10000, "advance")
    assert request.balance_due_on == CHECK_IN - timedelta(days=7)
    assert razorpay.links["plink_0001"]["amount"] == 300000
    assert request.hold_expires_at > datetime.now(timezone.utc) + timedelta(minutes=89)


async def test_a_property_override_changes_what_that_property_charges(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    prop.agent_config_overrides = {"payment": {"kind": "split", "split_percent": 50}}
    await db_session.commit()
    request = await _request(db_session, host, prop, total=8000)
    assert request.amount_due == 4000


async def test_a_second_guest_for_held_dates_is_told_they_are_on_hold(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    first = await _request(db_session, host, prop)

    with pytest.raises(DatesOnHold) as err:
        await _request(db_session, host, prop, guest="Ben", phone="+919822222222", ci=CHECK_IN + timedelta(days=1), co=CHECK_OUT + timedelta(days=1))

    assert err.value.hold.id == first.id and err.value.hold.guest_name == "Asha Rao"
    assert len([c for c in razorpay.seen if c[0] == "create"]) == 1  # no second link


async def test_two_guests_racing_for_the_same_dates_cannot_both_get_a_link(razorpay, whatsapp, monkeypatch):
    async with AsyncSessionLocal() as setup:
        host = await _host(setup)
        prop = await _property(setup, host)
        host_id, prop_id = host.id, prop.id

    # Widen the gap between "is it free?" and "hold it" so only the lock keeps
    # six guests from all seeing the dates as free.
    real_is_available = calendar_service.is_available

    async def slow_is_available(*args, **kwargs):
        free = await real_is_available(*args, **kwargs)
        await asyncio.sleep(0.05)
        return free

    monkeypatch.setattr(calendar_service, "is_available", slow_is_available)

    async def attempt(phone):
        async with AsyncSessionLocal() as db:
            user, property_ = await db.get(User, host_id), await db.get(Property, prop_id)
            return await _request(db, user, property_, guest=phone, phone=phone)

    results = await asyncio.gather(*(attempt(f"+9198000000{i:02d}") for i in range(6)), return_exceptions=True)

    winners = [r for r in results if isinstance(r, PaymentRequest)]
    assert len(winners) == 1
    assert all(isinstance(r, (DatesOnHold, DatesUnavailable)) for r in results if not isinstance(r, PaymentRequest))
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(PaymentRequest).where(PaymentRequest.property_id == prop_id)) == 1
    assert len([b for b in await _bookings(prop_id) if b.platform == "mira_hold"]) == 1


async def test_dates_taken_by_a_real_booking_are_unavailable_not_on_hold(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    db_session.add(Booking(property_id=prop.id, check_in=CHECK_IN, check_out=CHECK_OUT, platform="airbnb", source_uid="x", status="confirmed"))
    await db_session.commit()
    with pytest.raises(DatesUnavailable):
        await _request(db_session, host, prop)


async def test_the_calendar_sync_does_not_delete_a_hold(db_session, razorpay, whatsapp, monkeypatch):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    prop.ical_url = "https://example.com/feed.ics"
    await db_session.commit()
    await _request(db_session, host, prop)

    async def empty_feed(url):
        return []

    monkeypatch.setattr(calendar_service, "fetch_ical", empty_feed)
    await calendar_service.sync_property_ical(db_session, prop)

    assert await _available(prop.id) is False
    assert any(b.platform == "mira_hold" for b in await _bookings(prop.id))


async def test_if_razorpay_fails_the_dates_are_released(db_session, razorpay, whatsapp):
    razorpay.post(f"{API}/payment_links").mock(return_value=httpx.Response(500, json={"error": {"description": "boom"}}))
    host = await _host(db_session)
    prop = await _property(db_session, host)

    with pytest.raises(PaymentError, match="couldn't create"):
        await _request(db_session, host, prop)

    assert await _available(prop.id) is True
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(PaymentRequest.status)) == "cancelled"
    assert whatsapp.sent == []


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"phone": None}, "phone number"),
        ({"total": 0}, "no price"),
        ({"ci": date.today() - timedelta(days=2), "co": date.today() + timedelta(days=1)}, "aren't valid"),
        ({"ci": CHECK_OUT, "co": CHECK_IN}, "aren't valid"),
    ],
)
async def test_bad_requests_never_reach_razorpay(db_session, razorpay, whatsapp, kwargs, message):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    with pytest.raises(PaymentError, match=message):
        await _request(db_session, host, prop, **kwargs)
    assert [c for c in razorpay.seen if c[0] == "create"] == []


async def test_a_host_without_razorpay_cannot_create_links(db_session, razorpay, whatsapp):
    host = await _host(db_session, connected=False)
    prop = await _property(db_session, host)
    with pytest.raises(PaymentError, match="isn't set up"):
        await _request(db_session, host, prop)


async def test_if_whatsapp_refuses_the_host_is_asked_to_send_the_link(db_session, razorpay, whatsapp):
    whatsapp.outcome["accepted"] = False
    host = await _host(db_session)
    prop = await _property(db_session, host)

    request = await _request(db_session, host, prop)

    assert request.link_sent_at is None
    ticket = await db_session.scalar(select(Ticket).where(Ticket.user_id == host.id))
    assert ticket.kind == "payment" and ticket.urgency == "high" and "https://rzp.io/i/plink_0001" in ticket.detail


# ── the guest pays ────────────────────────────────────────────────────────


async def test_payment_is_seen_and_the_dates_become_a_confirmed_booking(db_session, razorpay, whatsapp):
    host = await _host(db_session, config=PAID_CONFIRMS)
    prop = await _property(db_session, host)
    lead = Lead(user_id=host.id, phone="+919811111111", guest_name="Asha Rao")
    db_session.add(lead)
    await db_session.commit()
    request = await _request(db_session, host, prop, lead_id=lead.id)
    whatsapp.sent.clear()

    pay(razorpay, "plink_0001")
    result = await payment_service.process_payments()

    assert result["paid"] == 1
    paid = await _fresh(request.id)
    assert paid.status == "paid" and paid.razorpay_payment_id == "pay_0001" and paid.paid_at and paid.success_sent_at
    [booking] = await _bookings(prop.id)
    assert (booking.kind, booking.platform, booking.status) == ("reservation", "mira", "confirmed")
    assert float(booking.final_booking_price) == 12000 and booking.price_status == "confirmed" and booking.price_confirmed_by == "mira"
    assert booking.mira_attribution_status == "confirmed" and booking.mira_attribution_lead_id == lead.id
    await db_session.refresh(lead)
    assert lead.status == "booked"
    guest_msgs = [t for p, t in whatsapp.sent if p == "+919811111111"]
    host_msgs = [t for p, t in whatsapp.sent if p == "+919800000001"]
    assert len(guest_msgs) == 1 and "received your payment of ₹12,000" in guest_msgs[0]
    assert len(host_msgs) == 1 and "The booking is confirmed" in host_msgs[0]
    assert await db_session.scalar(select(func.count()).select_from(Ticket).where(Ticket.user_id == host.id, Ticket.kind == "payment")) == 0


async def test_when_the_host_confirms_bookings_a_payment_asks_them_to(db_session, razorpay, whatsapp):
    host = await _host(db_session)  # default: host confirms
    prop = await _property(db_session, host)
    await _request(db_session, host, prop)
    pay(razorpay, "plink_0001")

    await payment_service.process_payments()

    [booking] = await _bookings(prop.id)
    assert booking.price_status == "pending_confirmation" and booking.price_confirmed_by is None
    ticket = await db_session.scalar(select(Ticket).where(Ticket.user_id == host.id, Ticket.kind == "payment"))
    assert ticket.urgency == "high" and "Confirm the booking" in ticket.summary
    assert await _available(prop.id) is False  # money is in: the dates stay blocked


async def test_a_payment_is_processed_exactly_once(db_session, razorpay, whatsapp):
    host = await _host(db_session, config=PAID_CONFIRMS)
    prop = await _property(db_session, host)
    await _request(db_session, host, prop)
    pay(razorpay, "plink_0001")
    whatsapp.sent.clear()

    for _ in range(3):
        await payment_service.process_payments()
    await asyncio.gather(payment_service.process_payments(), payment_service.process_payments())

    assert len(whatsapp.sent) == 2  # one to the guest, one to the host, however many sweeps ran
    assert len(await _bookings(prop.id)) == 1
    notes = await db_session.scalars(select(Notification).where(Notification.property_id == prop.id, Notification.message.like("Payment received%")))
    assert len(list(notes)) == 1


async def test_the_host_can_turn_off_the_whatsapp_when_paid(db_session, razorpay, whatsapp):
    host = await _host(db_session, config=PAID_CONFIRMS)
    await billing_service.set_notify_host(db_session, host, False)
    prop = await _property(db_session, host)
    await _request(db_session, host, prop)
    pay(razorpay, "plink_0001")
    whatsapp.sent.clear()

    await payment_service.process_payments()

    assert [p for p, _ in whatsapp.sent] == ["+919811111111"]  # the guest only
    assert await db_session.scalar(select(func.count()).select_from(Notification).where(Notification.message.like("Payment received%"))) == 1  # still in-app


async def test_an_unpaid_link_changes_nothing(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    request = await _request(db_session, host, prop)
    await payment_service.process_payments()
    assert (await _fresh(request.id)).status == "active"


# ── holds running out ─────────────────────────────────────────────────────


async def _lapse(request_id) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(update(PaymentRequest).where(PaymentRequest.id == request_id).values(hold_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await db.commit()


async def test_an_unpaid_hold_lapses_and_releases_the_dates(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    request = await _request(db_session, host, prop)
    await _lapse(request.id)

    result = await payment_service.process_payments()

    assert result["expired"] == 1
    assert (await _fresh(request.id)).status == "expired"
    assert await _available(prop.id) is True
    assert ("cancel", "plink_0001") in razorpay.seen  # the link can no longer be paid late
    assert razorpay.links["plink_0001"]["status"] == "cancelled"


async def test_waiting_guests_are_told_once_when_the_hold_lapses(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    request = await _request(db_session, host, prop)
    assert await payment_service.register_waiter(db_session, request, guest_name="Ben Kumar", guest_phone="+919822222222", lead_id=None, call_session_id=None) is True
    assert await payment_service.register_waiter(db_session, request, guest_name="Ben Kumar", guest_phone="+919822222222", lead_id=None, call_session_id=None) is False
    whatsapp.sent.clear()
    await _lapse(request.id)

    await payment_service.process_payments()
    await payment_service.process_payments()

    told = [(p, t) for p, t in whatsapp.sent if p == "+919822222222"]
    assert len(told) == 1 and "open again" in told[0][1] and "Sea View" in told[0][1]
    assert await db_session.scalar(select(func.count()).select_from(HoldWaiter).where(HoldWaiter.notified_at.is_not(None))) == 1


async def test_a_guest_who_pays_at_the_last_moment_keeps_the_dates(db_session, razorpay, whatsapp):
    host = await _host(db_session, config=PAID_CONFIRMS)
    prop = await _property(db_session, host)
    request = await _request(db_session, host, prop)
    await _lapse(request.id)
    pay(razorpay, "plink_0001")  # Razorpay now says paid; cancelling it will be refused

    await payment_service.process_payments()

    assert (await _fresh(request.id)).status == "paid"
    assert await _available(prop.id) is False


async def test_a_late_payment_for_still_free_dates_is_accepted(db_session, razorpay, whatsapp):
    host = await _host(db_session, config=PAID_CONFIRMS)
    prop = await _property(db_session, host)
    request = await _request(db_session, host, prop)
    await _lapse(request.id)
    async with AsyncSessionLocal() as db:  # the hold lapsed but the link could not be cancelled
        await payment_service._release(db, request.id, "expired")
    assert await _available(prop.id) is True

    pay(razorpay, "plink_0001")
    await payment_service.process_payments()

    assert (await _fresh(request.id)).status == "paid"
    assert await _available(prop.id) is False
    assert [b.kind for b in await _bookings(prop.id)] == ["reservation"]


async def test_a_late_payment_for_dates_someone_else_took_is_escalated_not_booked(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    request = await _request(db_session, host, prop)
    async with AsyncSessionLocal() as db:
        await payment_service._release(db, request.id, "expired")
    db_session.add(Booking(property_id=prop.id, check_in=CHECK_IN, check_out=CHECK_OUT, platform="airbnb", source_uid="taken", status="confirmed"))
    await db_session.commit()
    pay(razorpay, "plink_0001")
    whatsapp.sent.clear()

    await payment_service.process_payments()
    await payment_service.process_payments()

    assert (await _fresh(request.id)).status == "expired"  # not turned into a second booking
    assert len([b for b in await _bookings(prop.id) if b.platform == "airbnb"]) == 1
    tickets = list((await db_session.scalars(select(Ticket).where(Ticket.user_id == host.id, Ticket.kind == "payment"))).all())
    assert len(tickets) == 1 and tickets[0].urgency == "high" and "AFTER the hold" in tickets[0].detail
    assert len([t for p, t in whatsapp.sent if p == "+919800000001" and "DOUBLE BOOKING" in t]) == 1


# ── double bookings that slip through ─────────────────────────────────────


async def test_a_booking_that_lands_on_held_dates_is_escalated_once_at_high_urgency(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    await _request(db_session, host, prop)
    db_session.add(Booking(property_id=prop.id, check_in=CHECK_IN, check_out=CHECK_OUT, platform="airbnb", source_uid="late", status="confirmed"))
    await db_session.commit()

    first = await payment_service.process_payments()
    second = await payment_service.process_payments()

    assert (first["conflicts"], second["conflicts"]) == (1, 0)
    note = await db_session.scalar(select(Notification).where(Notification.property_id == prop.id, Notification.message.like("Double booking%")))
    assert note.urgency == "high" and note.channel == "escalation" and "Asha Rao" in note.message
    ticket = await db_session.scalar(select(Ticket).where(Ticket.user_id == host.id, Ticket.kind == "payment"))
    assert ticket.urgency == "high"


async def test_the_second_guest_interest_escalates_to_the_host_at_high_urgency(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    hold = await _request(db_session, host, prop)
    whatsapp.sent.clear()

    await payment_service.escalate_hold_interest(db_session, hold, prop, guest_name="Ben Kumar", guest_phone="+919822222222", call_session_id=None)

    note = await db_session.scalar(select(Notification).where(Notification.channel == "escalation"))
    assert note.urgency == "high" and "Ben Kumar" in note.message and "Asha Rao" in note.message and "on hold" in note.message
    ticket = await db_session.scalar(select(Ticket).where(Ticket.user_id == host.id, Ticket.kind == "payment"))
    assert ticket.urgency == "high"
    assert whatsapp.sent and whatsapp.sent[0][0] == "+919800000001"


# ── the host cancels, other hosts ─────────────────────────────────────────


async def test_the_host_can_cancel_a_hold_and_waiting_guests_hear(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    request = await _request(db_session, host, prop)
    await payment_service.register_waiter(db_session, request, guest_name="Ben", guest_phone="+919822222222", lead_id=None, call_session_id=None)
    whatsapp.sent.clear()

    assert await payment_service.cancel_request(db_session, host, request.id) is True

    assert (await _fresh(request.id)).status == "cancelled" and await _available(prop.id) is True
    assert [p for p, _ in whatsapp.sent] == ["+919822222222"]
    with pytest.raises(PaymentError, match="already cancelled"):
        await payment_service.cancel_request(db_session, host, request.id)


async def test_cancelling_when_the_guest_has_just_paid_keeps_the_dates(db_session, razorpay, whatsapp):
    host = await _host(db_session, config=PAID_CONFIRMS)
    prop = await _property(db_session, host)
    request = await _request(db_session, host, prop)
    pay(razorpay, "plink_0001")

    with pytest.raises(PaymentError, match="just paid"):
        await payment_service.cancel_request(db_session, host, request.id)

    assert (await _fresh(request.id)).status == "paid" and await _available(prop.id) is False


async def test_a_host_cannot_see_or_cancel_another_hosts_payments(db_session, razorpay, whatsapp):
    a, b = await _host(db_session, "Aaa"), await _host(db_session, "Bbb")
    request = await _request(db_session, a, await _property(db_session, a))

    with pytest.raises(PaymentError, match="not found"):
        await payment_service.cancel_request(db_session, b, request.id)
    assert await payment_service.list_requests(db_session, b.id) == []
    assert [r["id"] for r in await payment_service.list_requests(db_session, a.id)] == [request.id]
    assert (await _fresh(request.id)).status == "active"


async def test_keys_razorpay_stops_accepting_raise_one_alert_not_one_per_minute(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    await _request(db_session, host, prop)
    razorpay.get(url__regex=rf"{API}/payment_links/plink_\w+").mock(return_value=httpx.Response(401, json={"error": {"description": "bad"}}))

    for _ in range(3):
        await payment_service.process_payments()

    notes = list((await db_session.scalars(select(Notification).where(Notification.message.like("Razorpay isn't accepting%")))).all())
    assert len(notes) == 1 and notes[0].urgency == "high"


# ── each step happens once, and only to what it owns ─────────────────────


async def test_creating_a_hold_waits_for_the_property_lock(razorpay, whatsapp):
    from sqlalchemy import text

    async with AsyncSessionLocal() as setup:
        host = await _host(setup)
        prop = await _property(setup, host)
        host_id, prop_id = host.id, prop.id

    async def create():
        async with AsyncSessionLocal() as db:
            return await _request(db, await db.get(User, host_id), await db.get(Property, prop_id))

    async with AsyncSessionLocal() as holder:  # someone else is mid-way through holding this property
        await holder.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"hold:{prop_id}"})
        task = asyncio.create_task(create())
        await asyncio.sleep(0.4)
        assert not task.done()  # it is waiting its turn, not racing
        await holder.commit()
    assert (await asyncio.wait_for(task, 5)).status == "active"


async def test_two_sweeps_cannot_both_mark_the_same_payment_paid(razorpay, whatsapp):
    async with AsyncSessionLocal() as setup:
        host = await _host(setup, config=PAID_CONFIRMS)
        request = await _request(setup, host, await _property(setup, host))
        request_id = request.id
    whatsapp.sent.clear()

    async def mark():
        async with AsyncSessionLocal() as db:
            return await payment_service._mark_paid(db, request_id, "pay_0001")

    results = await asyncio.gather(*(mark() for _ in range(5)))

    assert sorted(results) == [False] * 4 + [True]
    assert len(whatsapp.sent) == 2  # one guest message, one host message, however many tried


async def test_two_sweeps_cannot_both_tell_a_waiting_guest(razorpay, whatsapp):
    async with AsyncSessionLocal() as setup:
        host = await _host(setup)
        request = await _request(setup, host, await _property(setup, host))
        await payment_service.register_waiter(setup, request, guest_name="Ben", guest_phone="+919822222222", lead_id=None, call_session_id=None)
        released = await payment_service._release(setup, request.id, "expired")
    whatsapp.sent.clear()

    async def notify():
        async with AsyncSessionLocal() as db:
            return await payment_service._notify_waiters(db, released)

    await asyncio.gather(*(notify() for _ in range(4)))

    assert len([1 for p, _ in whatsapp.sent if p == "+919822222222"]) == 1


async def test_releasing_a_hold_never_deletes_a_real_reservation(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    request = await _request(db_session, host, prop)
    real = Booking(property_id=prop.id, check_in=CHECK_IN + timedelta(days=40), check_out=CHECK_IN + timedelta(days=42), platform="airbnb", source_uid="real", status="confirmed")
    db_session.add(real)
    await db_session.commit()
    await db_session.execute(update(PaymentRequest).where(PaymentRequest.id == request.id).values(booking_id=real.id))  # a bad pointer
    await db_session.commit()

    await payment_service._release(db_session, request.id, "expired")

    assert any(b.platform == "airbnb" for b in await _bookings(prop.id))


# ── one bad payment must not stop the sweep ───────────────────────────────


async def test_one_failing_request_does_not_stop_the_others_in_the_sweep(db_session, razorpay, whatsapp, monkeypatch):
    host = await _host(db_session, config=PAID_CONFIRMS)
    first = await _request(db_session, host, await _property(db_session, host, "Sea View"))
    second = await _request(db_session, host, await _property(db_session, host, "Hill Cabin"), phone="+919822222222")
    pay(razorpay, "plink_0001")
    pay(razorpay, "plink_0002")
    real_fetch = razorpay_client.fetch_payment_link

    async def flaky(key_id, key_secret, link_id):
        if link_id == "plink_0001":
            raise RuntimeError("something unexpected")
        return await real_fetch(key_id, key_secret, link_id)

    monkeypatch.setattr(razorpay_client, "fetch_payment_link", flaky)

    await payment_service.process_payments()

    assert (await _fresh(second.id)).status == "paid"  # the one behind the failing request still got through
    assert (await _fresh(first.id)).status == "active"  # and the failing one is retried, not lost


async def test_one_failing_step_does_not_stop_the_other_steps(db_session, razorpay, whatsapp, monkeypatch):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    request = await _request(db_session, host, prop)
    await _lapse(request.id)

    async def broken(db):
        raise RuntimeError("the poll fell over")

    monkeypatch.setattr(payment_service, "_poll", broken)
    result = await payment_service.process_payments()

    assert result["expired"] == 1 and await _available(prop.id) is True  # holds still lapse


async def test_a_payment_interrupted_after_being_marked_paid_is_finished_by_the_sweep(db_session, razorpay, whatsapp, monkeypatch):
    host = await _host(db_session, config=PAID_CONFIRMS)
    prop = await _property(db_session, host)
    request = await _request(db_session, host, prop)
    pay(razorpay, "plink_0001")
    real_after = payment_service._after_payment
    state = {"fail": True}

    async def interrupted(db, request_id):
        if state["fail"]:
            raise RuntimeError("the worker died here")
        return await real_after(db, request_id)

    monkeypatch.setattr(payment_service, "_after_payment", interrupted)
    await payment_service.process_payments()

    assert (await _fresh(request.id)).status == "paid"
    assert [b.platform for b in await _bookings(prop.id)] == ["mira_hold"]  # money in, but still only a hold
    async with AsyncSessionLocal() as db:  # old enough for the repair step to take it
        await db.execute(update(PaymentRequest).where(PaymentRequest.id == request.id).values(paid_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
        await db.commit()
    state["fail"] = False
    whatsapp.sent.clear()

    first = await payment_service.process_payments()
    second = await payment_service.process_payments()

    assert (first["repaired"], second["repaired"]) == (1, 0)
    assert [(b.kind, b.platform) for b in await _bookings(prop.id)] == [("reservation", "mira")]
    assert len([p for p, _ in whatsapp.sent if p == "+919800000001"]) == 1 and len([p for p, _ in whatsapp.sent if p == "+919811111111"]) == 1


async def test_a_failure_after_the_money_is_in_does_not_resend_alerts_on_every_sweep(db_session, razorpay, whatsapp, monkeypatch):
    host = await _host(db_session, config=PAID_CONFIRMS)
    await _request(db_session, host, await _property(db_session, host))
    pay(razorpay, "plink_0001")
    whatsapp.sent.clear()
    real_render = billing_service.render

    def exploding_render(text, values):  # the guest receipt fails every time
        if "received your payment" in text:
            raise RuntimeError("template bug")
        return real_render(text, values)

    monkeypatch.setattr(billing_service, "render", exploding_render)
    for _ in range(3):
        await payment_service.process_payments()
    assert len([1 for p, _ in whatsapp.sent if p == "+919800000001"]) == 1  # the host heard once, not each minute


async def test_two_workers_finishing_the_same_payment_alert_the_host_once(razorpay, whatsapp):
    async with AsyncSessionLocal() as setup:
        host = await _host(setup, config=PAID_CONFIRMS)
        request = await _request(setup, host, await _property(setup, host))
        request_id = request.id
        await setup.execute(update(PaymentRequest).where(PaymentRequest.id == request_id).values(status="paid", paid_at=datetime.now(timezone.utc)))
        await setup.commit()
    whatsapp.sent.clear()

    async def finish():
        async with AsyncSessionLocal() as db:
            await payment_service._after_payment(db, request_id)

    await asyncio.gather(*(finish() for _ in range(5)))

    assert len([1 for p, _ in whatsapp.sent if p == "+919800000001"]) == 1
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Notification).where(Notification.message.like("Payment received%"))) == 1


# ── names, keys, accounts ─────────────────────────────────────────────────


async def test_a_blank_guest_name_does_not_stop_the_link(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)

    request = await _request(db_session, host, prop, guest="   ")

    assert request.guest_name is None and request.link_sent_at is not None
    assert whatsapp.sent[0][1].startswith("Hi there!")


async def test_a_waiting_guest_with_a_blank_name_does_not_break_the_sweep(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    prop = await _property(db_session, host)
    request = await _request(db_session, host, prop)
    await payment_service.register_waiter(db_session, request, guest_name="  ", guest_phone="+919822222222", lead_id=None, call_session_id=None)
    await _lapse(request.id)
    whatsapp.sent.clear()

    result = await payment_service.process_payments()

    assert result["expired"] == 1 and [t for p, t in whatsapp.sent if p == "+919822222222"][0].startswith("Hi there!")


async def test_cancelling_when_the_saved_key_cannot_be_read_says_so(db_session, razorpay, whatsapp, monkeypatch):
    host = await _host(db_session)
    request = await _request(db_session, host, await _property(db_session, host))
    monkeypatch.setattr(settings, "payment_encryption_key", Fernet.generate_key().decode())  # the key was rotated away

    with pytest.raises(PaymentError, match="can't be read"):
        await payment_service.cancel_request(db_session, host, request.id)


async def test_cannot_switch_razorpay_accounts_while_links_are_open_under_the_old_one(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    request = await _request(db_session, host, await _property(db_session, host))

    with pytest.raises(BillingError, match="still open under your current Razorpay account"):
        await billing_service.connect_razorpay(db_session, host, "rzp_live_Other12345678", "another-secret-value")
    assert billing_service.get_credentials(host) == (KEY_ID, KEY_SECRET)

    await billing_service.connect_razorpay(db_session, host, KEY_ID, "a-new-secret-for-the-same-account")  # same account: fine
    assert billing_service.get_credentials(host) == (KEY_ID, "a-new-secret-for-the-same-account")

    await payment_service.cancel_request(db_session, host, request.id)
    await billing_service.connect_razorpay(db_session, host, "rzp_live_Other12345678", "another-secret-value")
    assert billing_service.get_credentials(host)[0] == "rzp_live_Other12345678"


# ── did the link actually arrive? ─────────────────────────────────────────


async def _age_sent(request_id, seconds: int) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(update(PaymentRequest).where(PaymentRequest.id == request_id).values(link_sent_at=datetime.now(timezone.utc) - timedelta(seconds=seconds)))
        await db.commit()


async def test_a_link_whatsapp_could_not_deliver_is_reported_to_the_host_once(db_session, razorpay, whatsapp):
    whatsapp.outcome["delivery"] = ("undelivered", 63016)  # accepted by Twilio, then refused: the guest never messaged this number
    host = await _host(db_session)
    prop = await _property(db_session, host)
    request = await _request(db_session, host, prop)
    assert request.link_message_sid and request.link_sent_at
    await _age_sent(request.id, 60)

    first = await payment_service.process_payments()
    second = await payment_service.process_payments()

    assert (first["undelivered"], second["undelivered"]) == (1, 0)
    ticket = await db_session.scalar(select(Ticket).where(Ticket.user_id == host.id, Ticket.kind == "payment"))
    assert ticket.urgency == "high" and "https://rzp.io/i/plink_0001" in ticket.detail and "63016" in ticket.detail
    assert "first message" in ticket.detail  # says why, so the host knows what to do
    note = await db_session.scalar(select(Notification).where(Notification.message.like("The payment link didn't reach%")))
    assert note.urgency == "high"
    assert (await _fresh(request.id)).status == "active"  # the hold stands while the host sends it another way


async def test_a_delivered_link_raises_nothing(db_session, razorpay, whatsapp):
    host = await _host(db_session)
    request = await _request(db_session, host, await _property(db_session, host))
    await _age_sent(request.id, 60)

    result = await payment_service.process_payments()

    assert result["undelivered"] == 0 and (await _fresh(request.id)).link_delivery_checked_at is not None
    assert await db_session.scalar(select(func.count()).select_from(Ticket).where(Ticket.user_id == host.id)) == 0


async def test_a_link_still_on_its_way_is_rechecked_then_left_alone(db_session, razorpay, whatsapp):
    whatsapp.outcome["delivery"] = ("sent", None)  # e.g. the guest's phone is off
    host = await _host(db_session)
    request = await _request(db_session, host, await _property(db_session, host))

    await _age_sent(request.id, 60)
    await payment_service.process_payments()
    assert (await _fresh(request.id)).link_delivery_checked_at is None  # still watching

    await _age_sent(request.id, 400)
    await payment_service.process_payments()
    assert (await _fresh(request.id)).link_delivery_checked_at is not None  # stopped watching: it will arrive when the phone is on
    assert await db_session.scalar(select(func.count()).select_from(Ticket).where(Ticket.user_id == host.id)) == 0


async def test_if_twilio_cannot_be_asked_the_check_is_tried_again_next_minute(db_session, razorpay, whatsapp):
    whatsapp.outcome["delivery"] = RuntimeError("twilio is down")
    host = await _host(db_session)
    request = await _request(db_session, host, await _property(db_session, host))
    await _age_sent(request.id, 60)

    await payment_service.process_payments()
    assert (await _fresh(request.id)).link_delivery_checked_at is None

    whatsapp.outcome["delivery"] = ("delivered", None)
    await payment_service.process_payments()
    assert (await _fresh(request.id)).link_delivery_checked_at is not None


# ── small things ──────────────────────────────────────────────────────────


def test_rupees_become_paise_without_float_error():
    assert razorpay_client.to_paise(0.29) == 29 and razorpay_client.to_paise("1999.99") == 199999
    assert razorpay_client.to_paise(12345.5) == 1234550 and razorpay_client.to_paise(100) == 10000


def test_rupee_formatting():
    assert payment_service.rupees(12000) == "₹12,000" and payment_service.rupees("2499.5") == "₹2,499.50"


async def test_billing_endpoints_never_return_the_secret(client, db_session, razorpay):
    from tests.conftest import auth_headers_for

    host = await _host(db_session, connected=False)
    headers = auth_headers_for(host)

    saved = await client.put("/api/v1/billing/razorpay", headers=headers, json={"key_id": KEY_ID, "key_secret": KEY_SECRET})
    assert saved.status_code == 200 and KEY_SECRET not in saved.text and saved.json()["razorpay"]["connected"] is True
    assert KEY_SECRET not in (await client.get("/api/v1/billing", headers=headers)).text

    bad = await client.put("/api/v1/billing/razorpay", headers=headers, json={"key_id": "nope", "key_secret": KEY_SECRET})
    assert bad.status_code == 422 and KEY_SECRET not in bad.text

    assert (await client.put("/api/v1/billing/templates/payment_request", headers=headers, json={"text": "Pay {amount}: {link}"})).status_code == 200
    assert (await client.post("/api/v1/billing/templates/payment_request/approve", headers=headers)).json()["templates"]["payment_request"]["text"] == "Pay {amount}: {link}"
    assert (await client.put("/api/v1/billing/notify-host", headers=headers, json={"enabled": False})).json()["notify_host_on_payment"] is False
    assert (await client.get("/api/v1/payments", headers=headers)).json() == []
