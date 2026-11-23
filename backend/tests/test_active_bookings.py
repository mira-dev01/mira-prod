"""Leads -> Active bookings: who is booked, upcoming vs in stay, how they have paid, and requests from booked guests."""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.booking import Booking
from app.models.call_session import CallSession
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.payment import PaymentRequest
from app.models.property import Property
from app.models.user import User
from app.services import active_booking_service as ab
from app.services import notification_service
from app.utils.dates import today_ist

IST = ab.IST


def _day(offset: int) -> date:
    return today_ist() + timedelta(days=offset)


# -- the stay's phase -------------------------------------------------------------------------------


def test_a_stay_starts_at_the_properties_check_in_time_not_at_midnight():
    start, end = ab.stay_window(date(2026, 12, 8), date(2026, 12, 10), "15:30", "10:00")
    assert start == datetime(2026, 12, 8, 15, 30, tzinfo=IST) and end == datetime(2026, 12, 10, 10, 0, tzinfo=IST)
    assert ab.phase_of(datetime(2026, 12, 8, 15, 29, tzinfo=IST), start, end) == "upcoming"
    assert ab.phase_of(datetime(2026, 12, 8, 15, 30, tzinfo=IST), start, end) == "in_stay"
    assert ab.phase_of(datetime(2026, 12, 10, 9, 59, tzinfo=IST), start, end) == "in_stay"
    assert ab.phase_of(datetime(2026, 12, 10, 10, 0, tzinfo=IST), start, end) == "completed"


def test_missing_or_bad_times_fall_back_to_2pm_and_11am():
    start, end = ab.stay_window(date(2026, 12, 8), date(2026, 12, 9), None, "late")
    assert (start.hour, end.hour) == (14, 11)


def test_phone_keys_ignore_formatting_and_country_code():
    assert ab.phone_key("+91 98765-43210") == ab.phone_key("9876543210") == "9876543210"
    assert ab.phone_key("browser-test") == "" and ab.phone_key(None) == ""


# -- the payment tag ----------------------------------------------------------------------------------


def _request(status="active", **kw):
    base = dict(
        status=status, details={}, link_sent_at=datetime(2026, 1, 1), total_amount=Decimal(1000), amount_due=Decimal(1000),
        balance_due_on=None, paid_at=None, hold_expires_at=None, short_url="https://pay/x", id=uuid.uuid4(),
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    "request_, state",
    [
        (None, "no_link"),
        (_request(link_sent_at=None), "link_pending"),
        (_request(), "link_sent"),
        (_request(details={"link_delivery": "failed"}), "delivery_failed"),
        (_request(details={"failed_attempt_at": "2026-01-01T00:00:00"}), "payment_failed"),
        (_request("paid"), "paid"),
        (_request("paid", amount_due=Decimal(500)), "part_paid"),
        (_request("expired"), "expired"),
        (_request("cancelled"), "cancelled"),
    ],
)
def test_every_payment_state_has_a_tag(request_, state):
    assert ab.payment_state(request_) == state
    assert ab.payment_view(request_, provider_down=False)["label"]


def test_payment_down_is_flagged_only_while_a_link_is_waiting():
    assert ab.payment_view(_request(), provider_down=True)["payment_down"] is True
    assert ab.payment_view(_request("paid"), provider_down=True)["payment_down"] is False
    assert ab.payment_view(None, provider_down=True)["payment_down"] is False


def test_the_pay_link_is_only_shown_while_it_can_still_be_paid_and_a_split_shows_the_balance():
    assert ab.payment_view(_request(), False)["short_url"] == "https://pay/x"
    paid = ab.payment_view(_request("paid", amount_due=Decimal(400), balance_due_on=date(2026, 12, 1)), False)
    assert paid["short_url"] is None and paid["balance"] == 600 and paid["balance_due_on"] == "2026-12-01"


# -- the list -------------------------------------------------------------------------------------------


async def _lead(user, name, phone, check_in, check_out, status="closed", props=("Test Villa",), on_calendar=True):
    """A lead as Booking requests holds it. "closed" with `on_calendar` is what the host's Close dialog produces
    (the lead closed and its dates blocked on the calendar)."""
    async with AsyncSessionLocal() as db:
        lead = Lead(
            user_id=user.id, guest_name=name, phone=phone, check_in=check_in, check_out=check_out, status=status,
            properties_discussed=list(props), num_guests=3,
        )
        db.add(lead)
        await db.commit()
        await db.refresh(lead)
        if on_calendar and status == "closed" and check_in and check_out:
            prop_id = await db.scalar(select(Property.id).where(Property.user_id == user.id))
            if prop_id:
                db.add(Booking(property_id=prop_id, check_in=check_in, check_out=check_out, platform="manual", mira_attribution_lead_id=lead.id))
                await db.commit()
        return lead


async def _pay(user, prop, lead, status="paid", due=1000, total=1000, **details):
    async with AsyncSessionLocal() as db:
        request = PaymentRequest(
            user_id=user.id, property_id=prop.id, lead_id=lead.id, guest_name=lead.guest_name, guest_phone=lead.phone,
            check_in=lead.check_in, check_out=lead.check_out, total_amount=total, amount_due=due, kind="full", status=status,
            hold_expires_at=datetime(2030, 1, 1, tzinfo=IST), link_sent_at=datetime(2026, 1, 1, tzinfo=IST),
            short_url="https://pay/x", details=details or None, confirm_by="host",
        )
        db.add(request)
        await db.commit()
        return request


async def test_booked_guests_are_split_into_in_stay_and_upcoming_in_date_order(test_user, test_property):
    await _lead(test_user, "Later", "9000000003", _day(30), _day(32))
    await _lead(test_user, "Soon", "9000000002", _day(5), _day(7))
    await _lead(test_user, "Staying", "9000000001", _day(-1), _day(2))
    await _lead(test_user, "Left", "9000000004", _day(-9), _day(-6))  # stay over: not listed
    await _lead(test_user, "Just asking", "9000000005", _day(3), _day(4), status="open")  # not closed: not listed
    await _lead(test_user, "Dead end", "9000000006", _day(3), _day(4), on_calendar=False)  # closed, nothing booked
    await _lead(test_user, "No dates", "9000000007", None, None)  # closed without dates

    async with AsyncSessionLocal() as db:
        out = await ab.list_active(db, await db.get(User, test_user.id))

    assert [g["guest_name"] for g in out["in_stay"]] == ["Staying"]
    assert [g["guest_name"] for g in out["upcoming"]] == ["Soon", "Later"]  # soonest first
    assert out["in_stay"][0]["phase"] == "in_stay" and out["upcoming"][0]["phase"] == "upcoming"
    assert out["upcoming"][0]["payment"]["state"] == "no_link"
    assert out["active_when"] == "confirmed" and out["unread_activity"] == 0


async def test_each_guest_carries_the_state_of_their_payment(test_user, test_property):
    paid = await _lead(test_user, "Paid Pam", "9000000001", _day(5), _day(7))
    waiting = await _lead(test_user, "Waiting Wes", "9000000002", _day(6), _day(8))
    await _pay(test_user, test_property, paid, "paid")
    await _pay(test_user, test_property, waiting, "active", failed_attempt_at="2026-01-01T00:00:00")

    async with AsyncSessionLocal() as db:
        out = await ab.list_active(db, await db.get(User, test_user.id))

    states = {g["guest_name"]: g["payment"] for g in out["upcoming"]}
    assert states["Paid Pam"]["state"] == "paid" and states["Paid Pam"]["label"] == "Paid"
    assert states["Waiting Wes"]["state"] == "payment_failed" and states["Waiting Wes"]["short_url"] == "https://pay/x"
    assert out["upcoming"][0]["property_name"] == "Test Villa"


async def test_a_guest_whose_check_in_time_has_not_come_yet_is_still_upcoming_today(test_user, test_property):
    async with AsyncSessionLocal() as db:
        prop = await db.get(Property, test_property.id)
        now = datetime.now(IST)
        prop.check_in_time = (now + timedelta(hours=2)).strftime("%H:%M") if now.hour < 21 else "23:59"
        await db.commit()
    await _lead(test_user, "Arriving", "9000000001", today_ist(), today_ist() + timedelta(days=2))

    async with AsyncSessionLocal() as db:
        out = await ab.list_active(db, await db.get(User, test_user.id))

    assert [g["guest_name"] for g in out["upcoming"]] == ["Arriving"] and out["in_stay"] == []


async def test_another_hosts_bookings_are_never_listed(test_user, test_property):
    async with AsyncSessionLocal() as db:
        other = User(email="other@example.com", clerk_user_id="user_other", name="Other")
        db.add(other)
        await db.commit()
        await db.refresh(other)
    await _lead(other, "Not Mine", "9111111111", _day(3), _day(5))

    async with AsyncSessionLocal() as db:
        out = await ab.list_active(db, await db.get(User, test_user.id))

    assert out["upcoming"] == [] and out["in_stay"] == []


# -- requests from booked guests -----------------------------------------------------------------------------


async def _session(user, prop, phone):
    async with AsyncSessionLocal() as db:
        session = CallSession(
            exotel_call_id=f"call-{uuid.uuid4().hex[:8]}", user_id=user.id, property_id=prop.id, caller_number=phone, status="completed"
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session


async def _notify(prop, session, channel="escalation", **kw):
    async with AsyncSessionLocal() as db:
        return await notification_service.create_notification(
            db, channel=channel, message="Guest asked for extra towels", property_id=prop.id, call_session_id=session.id, **kw
        )


async def test_a_request_from_a_booked_guest_is_counted_and_clearing_it_resets_the_number(test_user, test_property):
    lead = await _lead(test_user, "Asha", "+91 98765 43210", _day(-1), _day(2))
    session = await _session(test_user, test_property, "9876543210")  # same guest, written differently

    first = await _notify(test_property, session)
    await _notify(test_property, session, channel="whatsapp")

    assert first.booking_lead_id == lead.id
    async with AsyncSessionLocal() as db:
        assert await ab.unread_count(db, test_user.id) == 2
        out = await ab.list_active(db, await db.get(User, test_user.id))
        assert out["unread_activity"] == 2 and out["in_stay"][0]["activity_count"] == 2
        assert await ab.mark_activity_read(db, test_user.id, lead.id) == 2
        assert await ab.unread_count(db, test_user.id) == 0


async def test_requests_from_other_callers_and_system_notices_are_not_counted(test_user, test_property):
    await _lead(test_user, "Asha", "9876543210", _day(-1), _day(2))
    stranger = await _session(test_user, test_property, "9123456789")
    guest = await _session(test_user, test_property, "9876543210")

    stranger_note = await _notify(test_property, stranger)
    system_note = await _notify(test_property, guest, channel="system")  # about the guest, not a request from them

    assert stranger_note.booking_lead_id is None and system_note.booking_lead_id is None


async def test_a_guest_whose_stay_is_over_or_not_booked_raises_no_count(test_user, test_property):
    await _lead(test_user, "Gone", "9000000009", _day(-9), _day(-6))
    await _lead(test_user, "Only asking", "9000000008", _day(3), _day(4), status="open")

    gone = await _notify(test_property, await _session(test_user, test_property, "9000000009"))
    asking = await _notify(test_property, await _session(test_user, test_property, "9000000008"))

    assert gone.booking_lead_id is None and asking.booking_lead_id is None


async def test_the_stay_request_notice_names_the_guest_even_without_a_call(test_user, test_property):
    lead = await _lead(test_user, "Asha", "9876543210", _day(2), _day(4))
    async with AsyncSessionLocal() as db:
        notification = await notification_service.create_notification(
            db, channel="system", message="Asha asked for a late check-out", property_id=test_property.id,
            guest_phone="+919876543210", from_guest=True,
        )
    assert notification.booking_lead_id == lead.id


async def test_clearing_someone_elses_guest_does_nothing(test_user, test_property):
    async with AsyncSessionLocal() as db:
        other = User(email="o2@example.com", clerk_user_id="user_o2", name="O")
        db.add(other)
        await db.commit()
        await db.refresh(other)
    lead = await _lead(test_user, "Asha", "9876543210", _day(-1), _day(2))
    await _notify(test_property, await _session(test_user, test_property, "9876543210"))

    async with AsyncSessionLocal() as db:
        assert await ab.mark_activity_read(db, other.id, lead.id) == 0
        assert await ab.unread_count(db, test_user.id) == 1


async def test_the_endpoints_return_the_list_the_count_and_clear(client, auth_headers, test_user, test_property):
    lead = await _lead(test_user, "Asha", "9876543210", _day(-1), _day(2))
    await _notify(test_property, await _session(test_user, test_property, "9876543210"))

    listing = (await client.get("/api/v1/bookings/active", headers=auth_headers)).json()
    assert listing["in_stay"][0]["guest_name"] == "Asha" and listing["unread_activity"] == 1
    assert (await client.get("/api/v1/bookings/active/activity-count", headers=auth_headers)).json() == {"count": 1}
    assert (await client.post(f"/api/v1/bookings/active/{lead.id}/activity/read", headers=auth_headers)).json() == {"cleared": 1}
    assert (await client.get("/api/v1/bookings/active/activity-count", headers=auth_headers)).json() == {"count": 0}


async def test_notifications_read_back_include_the_booking_link(client, auth_headers, test_user, test_property):
    lead = await _lead(test_user, "Asha", "9876543210", _day(-1), _day(2))
    await _notify(test_property, await _session(test_user, test_property, "9876543210"))

    rows = (await client.get("/api/v1/notifications", headers=auth_headers)).json()

    assert rows[0]["booking_lead_id"] == str(lead.id)
    async with AsyncSessionLocal() as db:
        stored = (await db.scalars(select(Notification))).all()
    assert len(stored) == 1


async def test_a_request_stops_counting_once_the_guest_is_no_longer_an_active_booking(test_user, test_property):
    lead = await _lead(test_user, "Asha", "9876543210", _day(-1), _day(2))
    await _notify(test_property, await _session(test_user, test_property, "9876543210"))
    async with AsyncSessionLocal() as db:
        assert await ab.unread_count(db, test_user.id) == 1

    async with AsyncSessionLocal() as db:  # the stay ends (or the host un-books the request)
        row = await db.get(Lead, lead.id)
        row.check_in, row.check_out = _day(-9), _day(-6)
        await db.commit()

    async with AsyncSessionLocal() as db:
        assert await ab.unread_count(db, test_user.id) == 0  # no row to clear it from, so no number either


async def test_a_notification_is_saved_already_tagged(test_user, test_property):
    lead = await _lead(test_user, "Asha", "9876543210", _day(-1), _day(2))
    note = await _notify(test_property, await _session(test_user, test_property, "9876543210"))

    async with AsyncSessionLocal() as db:
        stored = await db.get(Notification, note.id)
    assert stored.booking_lead_id == lead.id  # set in the same save: the live stream never sees it untagged


async def test_a_lead_booked_by_a_verified_payment_is_listed_but_a_booked_hot_lead_is_not(test_user, test_property):
    paid = await _lead(test_user, "Paid Pam", "9000000001", _day(5), _day(7), status="booked", on_calendar=False)
    hot = await _lead(test_user, "Hot Harry", "9000000002", _day(5), _day(7), status="booked", on_calendar=False)
    async with AsyncSessionLocal() as db:
        request = await _pay(test_user, test_property, paid, "paid")
        row = await db.get(PaymentRequest, request.id)
        row.confirm_by = "payment"
        await db.commit()

    async with AsyncSessionLocal() as db:
        out = await ab.list_active(db, await db.get(User, test_user.id))

    assert [g["guest_name"] for g in out["upcoming"]] == ["Paid Pam"]  # "Hot Lead" (booked) alone is no booking
    assert hot.id not in [uuid.UUID(g["lead_id"]) for g in out["upcoming"]]


async def test_closing_a_lead_in_booking_requests_is_what_puts_it_here(test_user, test_property):
    lead = await _lead(test_user, "Closing Cara", "9000000001", _day(5), _day(7), status="contacted", on_calendar=False)
    async with AsyncSessionLocal() as db:
        assert (await ab.list_active(db, await db.get(User, test_user.id)))["upcoming"] == []

    async with AsyncSessionLocal() as db:  # the Close dialog: block the dates, then set the lead to closed
        db.add(Booking(property_id=test_property.id, check_in=lead.check_in, check_out=lead.check_out, platform="manual", mira_attribution_lead_id=lead.id))
        row = await db.get(Lead, lead.id)
        row.status = "closed"
        await db.commit()

    async with AsyncSessionLocal() as db:
        out = await ab.list_active(db, await db.get(User, test_user.id))
    assert [g["guest_name"] for g in out["upcoming"]] == ["Closing Cara"]


# -- early check-in / late check-out (Spacez): the stay's window follows what the guest has paid for -------------------


def _stay_request(**kw):
    base = dict(want_early_checkin=False, want_late_checkout=False, note=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_a_paid_early_check_in_moves_the_start_earlier_and_never_later():
    from app.services import spacez_stay_requests as sr

    start, end = ab.stay_window(date(2026, 12, 8), date(2026, 12, 10), "14:00", "11:00")
    moved, same_end, early, late = sr.extend_window(start, end, date(2026, 12, 8), date(2026, 12, 10), [_stay_request(want_early_checkin=True, note="around 10 am")])
    assert moved == datetime(2026, 12, 8, 10, 0, tzinfo=IST) and same_end == end and early and not late

    default, _, _, _ = sr.extend_window(start, end, date(2026, 12, 8), date(2026, 12, 10), [_stay_request(want_early_checkin=True)])
    assert default == datetime(2026, 12, 8, 6, 0, tzinfo=IST)

    unchanged, _, flagged, _ = sr.extend_window(start, end, date(2026, 12, 8), date(2026, 12, 10), [_stay_request(want_early_checkin=True, note="3 pm")])
    assert unchanged == start and flagged  # an hour after check-in is not earlier: the window never shrinks


def test_a_paid_late_check_out_moves_the_end_later():
    from app.services import spacez_stay_requests as sr

    start, end = ab.stay_window(date(2026, 12, 8), date(2026, 12, 10), "14:00", "11:00")
    _, moved, early, late = sr.extend_window(start, end, date(2026, 12, 8), date(2026, 12, 10), [_stay_request(want_late_checkout=True, note="until 4:30 pm")])
    assert moved == datetime(2026, 12, 10, 16, 30, tzinfo=IST) and late and not early
    # a guest who is still there at 12:00 is in their stay, not checked out
    assert ab.phase_of(datetime(2026, 12, 10, 12, 0, tzinfo=IST), start, moved) == "in_stay"
    assert ab.phase_of(datetime(2026, 12, 10, 12, 0, tzinfo=IST), start, end) == "completed"


def test_one_note_for_a_request_wanting_both_is_not_read_as_either_hour():
    from app.services import spacez_stay_requests as sr

    start, end = ab.stay_window(date(2026, 12, 8), date(2026, 12, 10), "14:00", "11:00")
    moved, later, _, _ = sr.extend_window(start, end, date(2026, 12, 8), date(2026, 12, 10), [_stay_request(want_early_checkin=True, want_late_checkout=True, note="10 am")])
    assert moved.hour == 6 and later.hour == 18


async def _paid_stay_request(user, prop, payment, **kw):
    from app.models.spacez_stay_request import SpacezStayRequest

    async with AsyncSessionLocal() as db:
        db.add(
            SpacezStayRequest(
                user_id=user.id, property_id=prop.id, payment_request_id=payment.id, guest_phone=payment.guest_phone,
                check_in=payment.check_in, check_out=payment.check_out, status="paid", **kw,
            )
        )
        await db.commit()


async def test_a_guest_with_a_paid_early_check_in_is_checked_in_before_the_normal_time(test_user, test_property):
    lead = await _lead(test_user, "Early", "9000000011", _day(0), _day(2))
    payment = await _pay(test_user, test_property, lead)
    await _paid_stay_request(test_user, test_property, payment, want_early_checkin=True, note="12 am")  # in from midnight

    async with AsyncSessionLocal() as db:
        out = await ab.list_active(db, await db.get(User, test_user.id))
    assert [g["guest_name"] for g in out["in_stay"]] == ["Early"] and out["upcoming"] == []
    assert out["in_stay"][0]["early_checkin"] is True and out["in_stay"][0]["late_checkout"] is False


async def test_a_request_the_team_has_not_been_paid_for_does_not_move_the_stay(test_user, test_property):
    from app.models.spacez_stay_request import SpacezStayRequest

    lead = await _lead(test_user, "Unpaid", "9000000012", _day(3), _day(5))
    payment = await _pay(test_user, test_property, lead)
    async with AsyncSessionLocal() as db:
        db.add(SpacezStayRequest(
            user_id=test_user.id, property_id=test_property.id, payment_request_id=payment.id, guest_phone="9000000012",
            check_in=lead.check_in, check_out=lead.check_out, want_early_checkin=True, status="awaiting_payment",
        ))
        await db.commit()
        out = await ab.list_active(db, await db.get(User, test_user.id))
    assert [g["guest_name"] for g in out["upcoming"]] == ["Unpaid"] and out["upcoming"][0]["early_checkin"] is False


async def test_a_paid_late_check_out_keeps_the_guest_checked_in_on_the_last_day(test_user, test_property):
    lead = await _lead(test_user, "Late", "9000000013", _day(-2), _day(0))
    payment = await _pay(test_user, test_property, lead)
    await _paid_stay_request(test_user, test_property, payment, want_late_checkout=True, note="11:59 pm")

    async with AsyncSessionLocal() as db:
        out = await ab.list_active(db, await db.get(User, test_user.id))
    assert [g["guest_name"] for g in out["in_stay"]] == ["Late"] and out["in_stay"][0]["late_checkout"] is True


# -- tickets on the guest's own row ---------------------------------------------------------------------------------


async def _ticket(user, prop, session, kind="technician", summary="Fix the geyser", notification=None, status="open"):
    from app.models.ticket import Ticket

    async with AsyncSessionLocal() as db:
        ticket = Ticket(
            user_id=user.id, property_id=prop.id, call_session_id=session.id, kind=kind, summary=summary,
            notification_id=notification.id if notification else None, status=status,
        )
        db.add(ticket)
        await db.commit()
        await db.refresh(ticket)
        return ticket


async def test_a_booked_guests_open_tickets_are_on_their_row_and_a_ticket_is_not_also_a_request(test_user, test_property):
    from app.services import ticket_service

    lead = await _lead(test_user, "Asha", "9876543210", _day(-1), _day(2))
    session = await _session(test_user, test_property, "9876543210")
    ticketed = await _notify(test_property, session)  # the alert raised with the ticket
    await _notify(test_property, session, channel="whatsapp")  # a request nothing was ticketed for
    await _ticket(test_user, test_property, session, notification=ticketed)
    stranger = await _session(test_user, test_property, "9123456789")
    await _ticket(test_user, test_property, stranger, summary="Someone else's")

    async with AsyncSessionLocal() as db:
        out = await ab.list_active(db, await db.get(User, test_user.id))
        guest = out["in_stay"][0]
        assert [t["summary"] for t in guest["tickets"]] == ["Fix the geyser"] and guest["ticket_count"] == 1
        assert guest["activity_count"] == 1  # only the request with no ticket
        assert out["unread_activity"] == 1 and out["booked_open_tickets"] == 1
        assert await ticket_service.open_summary(db, test_user.id) == {"open": 2, "booked_open": 1}
        rows = await ticket_service.attach_booking(db, test_user.id, await ticket_service.list_tickets(db, test_user.id, "open"))
    mine = next(r for r in rows if r["summary"] == "Fix the geyser")
    assert mine["booking_lead_id"] == lead.id and mine["booking_phase"] == "in_stay"
    assert next(r for r in rows if r["summary"] == "Someone else's")["booking_lead_id"] is None


async def test_a_checked_out_guest_stays_on_the_list_only_while_a_ticket_is_open(test_user, test_property):
    await _lead(test_user, "Gone", "9000000014", _day(-9), _day(-6))
    session = await _session(test_user, test_property, "9000000014")
    ticket = await _ticket(test_user, test_property, session, summary="Return the deposit")

    async with AsyncSessionLocal() as db:
        out = await ab.list_active(db, await db.get(User, test_user.id))
        assert [(g["guest_name"], g["phase"]) for g in out["in_stay"]] == [("Gone", "checked_out")]
        from app.services import ticket_service

        await ticket_service.set_status(db, await db.get(type(ticket), ticket.id), "done")
        out = await ab.list_active(db, await db.get(User, test_user.id))
    assert out["in_stay"] == [] and out["upcoming"] == []


async def test_an_upcoming_guests_ticket_is_on_their_upcoming_row(test_user, test_property):
    await _lead(test_user, "Soon", "9000000015", _day(4), _day(6))
    session = await _session(test_user, test_property, "9000000015")
    await _ticket(test_user, test_property, session, kind="stay_request", summary="Early check-in?")
    async with AsyncSessionLocal() as db:
        out = await ab.list_active(db, await db.get(User, test_user.id))
    assert [t["summary"] for t in out["upcoming"][0]["tickets"]] == ["Early check-in?"]


async def test_the_tickets_summary_and_the_booking_mark_come_through_the_api(client, auth_headers, test_user, test_property):
    await _lead(test_user, "Asha", "9876543210", _day(-1), _day(2))
    session = await _session(test_user, test_property, "9876543210")
    await _ticket(test_user, test_property, session)

    summary = await client.get("/api/v1/tickets/summary", headers=auth_headers)
    assert summary.status_code == 200 and summary.json() == {"open": 1, "booked_open": 1}
    listed = (await client.get("/api/v1/tickets?state=open", headers=auth_headers)).json()
    assert listed[0]["booking_phase"] == "in_stay" and listed[0]["booking_lead_id"]
