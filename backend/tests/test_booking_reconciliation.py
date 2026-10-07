"""Price persistence (PriceEvent), iCal booking reconciliation, Mira
attribution, and the host confirmation endpoints -- see
app/services/booking_reconciliation_service.py for the rules under test."""

from datetime import date, timedelta

import respx
from httpx import Response
from sqlalchemy import select

from app.models.booking import Booking
from app.models.call_session import CallSession
from app.models.lead import Lead
from app.models.price_event import PriceEvent
from app.schemas.tool import GetPricingArgs, NegotiateRateArgs
from app.services import booking_reconciliation_service, lead_service, price_event_service, tool_handlers
from app.services.calendar_service import sync_property_ical
from app.utils.dates import today_ist

GUEST_PHONE = "+919876543210"


def _ics(*events: tuple[str, date, date, str, str | None]) -> str:
    body = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Airbnb Inc//Hosting Calendar 0.8.8//EN"]
    for uid, check_in, check_out, summary, last4 in events:
        body += [
            "BEGIN:VEVENT",
            f"DTSTART;VALUE=DATE:{check_in:%Y%m%d}",
            f"DTEND;VALUE=DATE:{check_out:%Y%m%d}",
            f"UID:{uid}",
            f"SUMMARY:{summary}",
        ]
        if last4:
            body.append(
                "DESCRIPTION:Reservation URL: https://www.airbnb.com/hosting/reservations/details/HM123\\n"
                f"Phone Number (Last 4 Digits): {last4}"
            )
        body.append("END:VEVENT")
    body.append("END:VCALENDAR")
    return "\r\n".join(body)


def _stay() -> tuple[date, date]:
    check_in = today_ist() + timedelta(days=21)
    return check_in, check_in + timedelta(days=2)


async def _conversation(db, user, property_, *, phone=GUEST_PHONE, accept=True, negotiate=True, dates=None):
    """A real-shaped Mira conversation: a call, its lead, a quote and
    (optionally) a negotiation, written through the actual tool handlers."""
    check_in, check_out = dates or _stay()
    call = CallSession(
        user_id=user.id,
        property_id=property_.id,
        caller_number=phone,
        status="completed",
        call_type="BOOKING_LEAD",
    )
    db.add(call)
    await db.commit()
    await lead_service.upsert_lead(
        db,
        user.id,
        call.id,
        guest_name="Asha",
        phone=phone,
        check_in=check_in,
        check_out=check_out,
        num_guests=2,
        properties_discussed=[property_.name],
    )
    await tool_handlers.handle_get_pricing(
        db,
        GetPricingArgs(property_id=str(property_.id), check_in=check_in, check_out=check_out, num_guests=2),
        host_user_id=user.id,
        call_session_id=call.id,
    )
    await price_event_service.drain_pending_writes()
    if negotiate:
        quote = await db.scalar(select(PriceEvent).where(PriceEvent.call_session_id == call.id))
        offer = round(float(quote.price) * (0.95 if accept else 0.5))
        await tool_handlers.handle_negotiate_rate(
            db,
            NegotiateRateArgs(
                property_id=str(property_.id),
                check_in=check_in,
                check_out=check_out,
                guest_offer=offer,
                guest_loyalty="new",
            ),
            host_user_id=user.id,
            call_session_id=call.id,
        )
        await price_event_service.drain_pending_writes()
    return call.id


async def _sync(db, property_, ics: str) -> list[Booking]:
    property_.ical_url = f"https://airbnb.com/calendar/ical/{property_.id}.ics"
    await db.commit()
    property_id = property_.id
    with respx.mock:
        respx.get(property_.ical_url).mock(return_value=Response(200, text=ics))
        await sync_property_ical(db, property_)
    db.expire_all()
    rows = list((await db.scalars(select(Booking).where(Booking.property_id == property_id))).all())
    await db.refresh(property_)
    return rows


# --- Pricing persistence ---------------------------------------------------


async def test_get_pricing_persists_initial_quote(db_session, test_user, test_property):
    call_id = await _conversation(db_session, test_user, test_property, negotiate=False)
    events = (await db_session.scalars(select(PriceEvent).where(PriceEvent.call_session_id == call_id))).all()
    assert [e.price_type for e in events] == ["initial_quote"]
    assert events[0].source == "mira_conversation"
    assert events[0].property_id == test_property.id
    assert events[0].nights == 2
    assert float(events[0].price) > 0


async def test_negotiate_rate_persists_counter_and_accepted_offers_separately(db_session, test_user, test_property):
    rejected_id = await _conversation(db_session, test_user, test_property, accept=False, phone="+919000000001")
    accepted_id = await _conversation(db_session, test_user, test_property, accept=True, phone="+919000000002")

    rejected_types = [
        e.price_type
        for e in (await db_session.scalars(select(PriceEvent).where(PriceEvent.call_session_id == rejected_id))).all()
    ]
    accepted_events = (
        await db_session.scalars(select(PriceEvent).where(PriceEvent.call_session_id == accepted_id))
    ).all()
    assert sorted(rejected_types) == ["counter_offer", "initial_quote"]
    offer = next(e for e in accepted_events if e.price_type == "accepted_offer")
    assert offer.list_price is not None and float(offer.price) < float(offer.list_price)
    assert offer.guest_offer is not None


async def test_price_event_write_failure_never_raises(monkeypatch):
    class _Broken:
        def __call__(self):
            raise RuntimeError("db down")

    import app.database

    monkeypatch.setattr(app.database, "AsyncSessionLocal", _Broken())
    await price_event_service.record_price_event(
        user_id=__import__("uuid").uuid4(), price_type="initial_quote", source="mira_conversation", price=100
    )


# --- iCal booking reconciliation -------------------------------------------


async def test_new_ical_booking_without_conversation_is_pending_and_not_attributed(db_session, test_property):
    check_in, check_out = _stay()
    [booking] = await _sync(db_session, test_property, _ics(("res-1", check_in, check_out, "Reserved", "3210")))
    assert booking.kind == "reservation"
    assert booking.guest_phone_last4 == "3210"
    assert booking.final_booking_price is None
    assert booking.price_status == "pending_confirmation"
    assert booking.mira_attribution_status == "not_attributed"
    assert booking.needs_price_confirmation


async def test_ical_block_is_not_a_reservation(db_session, test_property):
    check_in, check_out = _stay()
    [booking] = await _sync(
        db_session, test_property, _ics(("blk-1", check_in, check_out, "Airbnb (Not available)", None))
    )
    assert booking.kind == "blocked"
    assert not booking.needs_price_confirmation
    assert booking.mira_attribution_status == "unknown"


async def test_matching_conversation_with_accepted_offer_sets_confident_final_price(
    db_session, test_user, test_property
):
    call_id = await _conversation(db_session, test_user, test_property, accept=True)
    check_in, check_out = _stay()
    [booking] = await _sync(db_session, test_property, _ics(("res-2", check_in, check_out, "Reserved", "3210")))

    accepted = await db_session.scalar(
        select(PriceEvent).where(PriceEvent.call_session_id == call_id, PriceEvent.price_type == "accepted_offer")
    )
    assert booking.mira_attribution_status == "probable"
    assert booking.mira_attribution_call_session_id == call_id
    assert {"same_property", "exact_dates", "phone_last4_match"} <= set(booking.mira_attribution_signals)
    assert float(booking.final_booking_price) == float(accepted.price)
    assert booking.price_source == "mira_conversation"
    assert booking.price_status == "confirmed"
    assert booking.price_confirmed_by == "mira"
    assert booking.initial_price is not None and booking.negotiated_price == accepted.price
    linked = (await db_session.scalars(select(PriceEvent).where(PriceEvent.booking_id == booking.id))).all()
    assert {e.price_type for e in linked} >= {"initial_quote", "accepted_offer", "final_price"}


async def test_matching_conversation_without_accepted_offer_stays_pending_with_detected_price(
    db_session, test_user, test_property
):
    await _conversation(db_session, test_user, test_property, accept=False)
    check_in, check_out = _stay()
    [booking] = await _sync(db_session, test_property, _ics(("res-3", check_in, check_out, "Reserved", "3210")))
    assert booking.mira_attribution_status == "probable"
    assert booking.final_booking_price is None
    assert booking.price_status == "pending_confirmation"
    assert booking.detected_price == float(booking.negotiated_price)
    assert booking.needs_price_confirmation and booking.needs_attribution_review


async def test_accepted_offer_without_identity_match_is_not_auto_confirmed(db_session, test_user, test_property):
    # Same property, exact dates, accepted offer -- but the Airbnb booking
    # carries no phone, so nothing ties it to THIS guest. Probable, not a
    # confirmed price.
    await _conversation(db_session, test_user, test_property, accept=True)
    check_in, check_out = _stay()
    [booking] = await _sync(db_session, test_property, _ics(("res-4", check_in, check_out, "Reserved", None)))
    assert booking.mira_attribution_status == "probable"
    assert booking.final_booking_price is None
    assert booking.price_status == "pending_confirmation"


async def test_same_dates_alone_is_not_a_probable_match(db_session, test_user, test_property):
    # A cold browsing lead that happened to mention the same dates and
    # property, with no pricing and no identity link: not enough evidence.
    check_in, check_out = _stay()
    call = CallSession(user_id=test_user.id, property_id=test_property.id, caller_number=None, status="completed")
    db_session.add(call)
    await db_session.commit()
    call_id = call.id
    await lead_service.upsert_lead(
        db_session,
        test_user.id,
        call_id,
        check_in=check_in,
        check_out=check_out,
        lead_temperature="cold",
        properties_discussed=[test_property.name],
    )
    lead = await db_session.scalar(select(Lead).where(Lead.call_session_id == call_id))
    lead.lead_temperature = "cold"  # bypass the warm floor to model a genuinely cold enquiry
    await db_session.commit()

    [booking] = await _sync(db_session, test_property, _ics(("res-5", check_in, check_out, "Reserved", None)))
    assert booking.mira_attribution_status == "unknown"
    assert booking.mira_attribution_lead_id is None
    assert not booking.needs_attribution_review


async def test_phone_mismatch_disqualifies_the_conversation(db_session, test_user, test_property):
    await _conversation(db_session, test_user, test_property, accept=True)
    check_in, check_out = _stay()
    [booking] = await _sync(db_session, test_property, _ics(("res-6", check_in, check_out, "Reserved", "1111")))
    assert booking.mira_attribution_status != "probable"
    assert booking.final_booking_price is None


async def test_reconciliation_failure_never_breaks_the_sync(db_session, test_property, monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("reconciliation bug")

    monkeypatch.setattr(booking_reconciliation_service, "reconcile_booking", _boom)
    check_in, check_out = _stay()
    [booking] = await _sync(db_session, test_property, _ics(("res-7", check_in, check_out, "Reserved", None)))
    assert booking.status == "confirmed"
    assert booking.price_status == "pending_confirmation"


async def test_resync_never_overwrites_host_decisions(db_session, test_user, test_property):
    user_id = test_user.id
    check_in, check_out = _stay()
    ics = _ics(("res-8", check_in, check_out, "Reserved", None))
    [booking] = await _sync(db_session, test_property, ics)
    await booking_reconciliation_service.confirm_price(db_session, booking, user_id, "outside_mira", 9000)
    [booking] = await _sync(db_session, test_property, ics)
    assert float(booking.final_booking_price) == 9000
    assert booking.price_source == "host_entered"


# --- Host confirmation API --------------------------------------------------


async def test_host_confirms_mira_match_yes(client, auth_headers, db_session, test_user, test_property):
    call_id = await _conversation(db_session, test_user, test_property, accept=False)
    check_in, check_out = _stay()
    [booking] = await _sync(db_session, test_property, _ics(("res-9", check_in, check_out, "Reserved", "3210")))

    resp = await client.patch(
        f"/api/v1/bookings/{booking.id}/attribution", json={"is_mira_match": True}, headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["booking"]["mira_attribution_status"] == "confirmed"
    assert body["booking"]["needs_attribution_review"] is False
    assert body["conversation"]["call_session_id"] == str(call_id)
    lead_id = booking.mira_attribution_lead_id
    db_session.expire_all()
    lead = await db_session.get(Lead, lead_id)
    assert lead.status == "booked"


async def test_host_rejects_mira_match_withdraws_conversation_prices(
    client, auth_headers, db_session, test_user, test_property
):
    await _conversation(db_session, test_user, test_property, accept=True)
    check_in, check_out = _stay()
    [booking] = await _sync(db_session, test_property, _ics(("res-10", check_in, check_out, "Reserved", "3210")))
    assert booking.price_confirmed_by == "mira"

    resp = await client.patch(
        f"/api/v1/bookings/{booking.id}/attribution", json={"is_mira_match": False}, headers=auth_headers
    )
    body = resp.json()["booking"]
    assert body["mira_attribution_status"] == "not_attributed"
    assert body["final_booking_price"] is None
    assert body["price_status"] == "pending_confirmation"
    assert body["initial_price"] is None and body["negotiated_price"] is None
    booking_id = booking.id
    db_session.expire_all()
    linked = (await db_session.scalars(select(PriceEvent).where(PriceEvent.booking_id == booking_id))).all()
    assert all(e.source != "mira_conversation" for e in linked)


async def test_attribution_decision_requires_a_matched_conversation(client, auth_headers, db_session, test_property):
    check_in, check_out = _stay()
    [booking] = await _sync(db_session, test_property, _ics(("res-11", check_in, check_out, "Reserved", None)))
    resp = await client.patch(
        f"/api/v1/bookings/{booking.id}/attribution", json={"is_mira_match": True}, headers=auth_headers
    )
    assert resp.status_code == 422


async def test_price_confirmation_options(client, auth_headers, db_session, test_user, test_property):
    await _conversation(db_session, test_user, test_property, accept=False)
    check_in, check_out = _stay()
    [booking] = await _sync(db_session, test_property, _ics(("res-12", check_in, check_out, "Reserved", "3210")))
    url = f"/api/v1/bookings/{booking.id}/price"

    # Option 1 with no amount = the detected price, confirmed by the host.
    resp = await client.patch(url, json={"choice": "mira_conversation"}, headers=auth_headers)
    body = resp.json()["booking"]
    assert body["final_booking_price"] == float(booking.negotiated_price)
    assert body["price_source"] == "host_confirmed"
    assert body["price_status"] == "confirmed"
    assert body["price_confirmed_by"] == "host"
    assert body["price_confirmed_at"] is not None

    # Option 2: the host types the real price.
    resp = await client.patch(
        url, json={"choice": "outside_mira", "final_booking_price": 12345}, headers=auth_headers
    )
    body = resp.json()["booking"]
    assert body["final_booking_price"] == 12345
    assert body["price_source"] == "host_entered"

    # outside_mira without an amount is rejected, not stored as 0.
    resp = await client.patch(url, json={"choice": "outside_mira"}, headers=auth_headers)
    assert resp.status_code == 422


async def test_not_confirmed_yet_leaves_price_missing_but_stops_asking(
    client, auth_headers, db_session, test_property
):
    check_in, check_out = _stay()
    [booking] = await _sync(db_session, test_property, _ics(("res-13", check_in, check_out, "Reserved", None)))

    queue = (await client.get("/api/v1/bookings/reconciliation", headers=auth_headers)).json()
    assert [item["booking"]["id"] for item in queue["items"]] == [str(booking.id)]
    assert queue["price_missing"] == 1

    resp = await client.patch(
        f"/api/v1/bookings/{booking.id}/price", json={"choice": "not_confirmed_yet"}, headers=auth_headers
    )
    body = resp.json()["booking"]
    assert body["final_booking_price"] is None
    assert body["price_status"] == "pending_confirmation"
    assert body["needs_price_confirmation"] is False

    queue = (await client.get("/api/v1/bookings/reconciliation", headers=auth_headers)).json()
    assert queue["total"] == 0


async def test_confirmed_booking_leaves_the_queue(client, auth_headers, db_session, test_property):
    check_in, check_out = _stay()
    [booking] = await _sync(db_session, test_property, _ics(("res-14", check_in, check_out, "Reserved", None)))
    await client.patch(
        f"/api/v1/bookings/{booking.id}/price",
        json={"choice": "outside_mira", "final_booking_price": 8000},
        headers=auth_headers,
    )
    queue = (await client.get("/api/v1/bookings/reconciliation", headers=auth_headers)).json()
    assert queue["total"] == 0


async def test_host_marks_reservation_as_blocked_dates(client, auth_headers, db_session, test_property):
    check_in, check_out = _stay()
    [booking] = await _sync(db_session, test_property, _ics(("res-15", check_in, check_out, "Reserved", None)))
    resp = await client.patch(f"/api/v1/bookings/{booking.id}/kind", json={"kind": "blocked"}, headers=auth_headers)
    assert resp.json()["booking"]["kind"] == "blocked"
    queue = (await client.get("/api/v1/bookings/reconciliation", headers=auth_headers)).json()
    assert queue["total"] == 0


async def test_manual_booking_with_price_is_host_entered(client, auth_headers, test_property):
    check_in, check_out = _stay()
    resp = await client.post(
        "/api/v1/bookings",
        json={
            "property_id": str(test_property.id),
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "final_booking_price": 7500,
        },
        headers=auth_headers,
    )
    body = resp.json()
    assert resp.status_code == 201
    assert body["final_booking_price"] == 7500
    assert body["price_source"] == "host_entered"
    assert body["price_status"] == "confirmed"


async def test_booking_created_by_closing_a_mira_lead_is_confirmed_attribution(
    client, auth_headers, db_session, test_user, test_property
):
    call_id = await _conversation(db_session, test_user, test_property, accept=False)
    lead = await db_session.scalar(select(Lead).where(Lead.call_session_id == call_id))
    check_in, check_out = _stay()
    resp = await client.post(
        "/api/v1/bookings",
        json={
            "property_id": str(test_property.id),
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "guest_phone": GUEST_PHONE,
            "lead_id": str(lead.id),
        },
        headers=auth_headers,
    )
    body = resp.json()
    assert body["mira_attribution_status"] == "confirmed"
    assert body["mira_attribution_lead_id"] == str(lead.id)
    assert "host_closed_lead" in body["mira_attribution_signals"]
    assert body["negotiated_price"] is not None
    assert body["price_status"] == "pending_confirmation"


async def test_booking_cannot_be_linked_to_another_hosts_lead(client, auth_headers, db_session, test_property):
    from app.models.user import User

    other = User(email="other@example.com", clerk_user_id="user_other_x", name="Other")
    db_session.add(other)
    await db_session.commit()
    other_lead = Lead(user_id=other.id)
    db_session.add(other_lead)
    await db_session.commit()
    check_in, check_out = _stay()
    resp = await client.post(
        "/api/v1/bookings",
        json={
            "property_id": str(test_property.id),
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "lead_id": str(other_lead.id),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 404


async def test_manual_price_is_recorded_in_price_history(client, auth_headers, db_session, test_property):
    check_in, check_out = _stay()
    resp = await client.post(
        "/api/v1/bookings",
        json={
            "property_id": str(test_property.id),
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "final_booking_price": 6400,
        },
        headers=auth_headers,
    )
    booking_id = resp.json()["id"]
    events = (
        await db_session.scalars(select(PriceEvent).where(PriceEvent.booking_id == __import__("uuid").UUID(booking_id)))
    ).all()
    assert [(e.price_type, e.source, float(e.price)) for e in events] == [("final_price", "host_entered", 6400.0)]
