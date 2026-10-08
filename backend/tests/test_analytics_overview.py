"""GET /analytics/overview (analytics_service.overview): reporting-period
metrics follow the selected dates exactly (night boundaries, partial
overlap, month/year crossings, multiple properties), current-state items
ignore them, and the browser-test toggle only touches conversation-derived
numbers."""

import uuid
from datetime import date, timedelta

import pytest

from app.models.booking import Booking
from app.models.call_session import CallSession
from app.models.lead import Lead
from app.models.price_event import PriceEvent
from app.models.property import Property
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER
from app.utils.dates import today_ist


def _booking(property_, check_in, check_out, *, price=None, **extra) -> Booking:
    fields = dict(
        property_id=property_.id,
        check_in=check_in,
        check_out=check_out,
        platform="airbnb",
        source_uid=uuid.uuid4().hex,
        status="confirmed",
        kind="reservation",
        price_status="pending_confirmation",
    )
    if price is not None:
        fields.update(final_booking_price=price, price_status="confirmed", price_source="host_entered")
    fields.update(extra)
    return Booking(**fields)


async def _overview(client, headers, start: date, end: date, **params) -> dict:
    resp = await client.get(
        "/api/v1/analytics/overview",
        params={"start_date": start.isoformat(), "end_date": end.isoformat(), **params},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.parametrize(
    ("start", "end", "booked_nights", "available_nights"),
    [
        # Booking 18 Oct -> 20 Oct occupies the nights of the 18th and 19th.
        (date(2026, 10, 18), date(2026, 10, 18), 1, 1),  # 1-day range
        (date(2026, 10, 18), date(2026, 10, 20), 2, 3),  # checkout day is not a booked night
        (date(2026, 10, 19), date(2026, 10, 19), 1, 1),
        (date(2026, 10, 20), date(2026, 10, 20), 0, 1),  # checkout day alone
        (date(2026, 10, 14), date(2026, 10, 20), 2, 7),  # 7-day range
        (date(2026, 10, 1), date(2026, 10, 17), 0, 17),  # range before the stay: no data
    ],
)
async def test_booked_night_boundaries(client, auth_headers, db_session, test_property, start, end, booked_nights, available_nights):
    db_session.add(_booking(test_property, date(2026, 10, 18), date(2026, 10, 20), price=6000))
    await db_session.commit()
    body = await _overview(client, auth_headers, start, end)
    assert body["portfolio"]["booked_nights"] == booked_nights
    assert body["portfolio"]["available_nights"] == available_nights


async def test_one_night_window_apportions_revenue(client, auth_headers, db_session, test_property):
    db_session.add(_booking(test_property, date(2026, 10, 18), date(2026, 10, 20), price=6000))
    await db_session.commit()
    body = await _overview(client, auth_headers, date(2026, 10, 18), date(2026, 10, 18))
    assert body["portfolio"]["revenue"] == 3000.0  # 1 of the stay's 2 nights
    assert body["portfolio"]["adr"] == 3000.0
    assert body["portfolio"]["occupancy"] == 1.0


async def test_stay_crossing_month_and_year_boundaries(client, auth_headers, db_session, test_property):
    # 4 nights: Dec 30, Dec 31, Jan 1, Jan 2 at 8000 total.
    db_session.add(_booking(test_property, date(2026, 12, 30), date(2027, 1, 3), price=8000))
    await db_session.commit()
    december = await _overview(client, auth_headers, date(2026, 12, 1), date(2026, 12, 31))
    january = await _overview(client, auth_headers, date(2027, 1, 1), date(2027, 1, 31))
    across = await _overview(client, auth_headers, date(2026, 12, 25), date(2027, 1, 5))
    assert december["portfolio"]["booked_nights"] == 2 and december["portfolio"]["revenue"] == 4000.0
    assert january["portfolio"]["booked_nights"] == 2 and january["portfolio"]["revenue"] == 4000.0
    assert across["portfolio"]["booked_nights"] == 4 and across["portfolio"]["revenue"] == 8000.0


async def test_multi_property_portfolio_aggregation(client, auth_headers, db_session, test_user, test_property):
    palm = Property(user_id=test_user.id, name="Palm House", base_price=5000)
    db_session.add(palm)
    await db_session.commit()
    db_session.add_all(
        [
            _booking(test_property, date(2026, 11, 1), date(2026, 11, 3), price=6000),  # 2 nights
            # Two overlapping calendar rows for the same nights: counted once.
            _booking(test_property, date(2026, 11, 2), date(2026, 11, 3)),
            _booking(palm, date(2026, 11, 1), date(2026, 11, 2), price=3000),  # 1 night
        ]
    )
    await db_session.commit()
    body = await _overview(client, auth_headers, date(2026, 11, 1), date(2026, 11, 4))
    p = body["portfolio"]
    assert p["available_nights"] == 8
    assert p["booked_nights"] == 3
    assert p["occupancy"] == round(3 / 8, 4)
    assert p["revenue"] == 9000.0
    assert p["adr"] == 3000.0
    assert p["revpar"] == round(9000 / 8, 2)
    assert p["completeness"]["bookings_total"] == 3
    assert p["completeness"]["bookings_priced"] == 2


async def test_no_data_range_distinguishes_unavailable_from_zero(client, auth_headers, test_property):
    body = await _overview(client, auth_headers, date(2026, 3, 1), date(2026, 3, 7))
    assert body["has_properties"] is True
    assert body["portfolio"]["occupancy"] == 0.0  # a real zero: 7 available nights, none booked
    assert body["portfolio"]["revenue"] is None  # not ₹0: there is simply no priced booking
    assert body["portfolio"]["adr"] is None
    assert body["opportunities"]["booking_opportunities"] == 0
    assert body["mira_attributed_bookings"] == 0


async def test_no_properties(client, auth_headers):
    body = await _overview(client, auth_headers, date(2026, 3, 1), date(2026, 3, 7))
    assert body["has_properties"] is False
    assert body["portfolio"]["occupancy"] is None


async def _call(db, user, property_, *, lead=None, caller="+919811111111", call_type="BOOKING_LEAD"):
    call = CallSession(
        user_id=user.id,
        property_id=property_.id,
        caller_number=caller,
        status="completed",
        call_type=call_type,
        lead_id=lead.id if lead else None,
    )
    db.add(call)
    await db.commit()
    return call


async def test_booking_opportunities_and_test_call_toggle(client, auth_headers, db_session, test_user, test_property):
    warm = Lead(user_id=test_user.id, lead_temperature="warm", phone="+919811111111")
    # Like production: a browser-test lead has NO phone (the pipeline
    # withholds the test identity from tools) -- only its call is marked.
    hot = Lead(user_id=test_user.id, lead_temperature="very_hot", phone=None)
    db_session.add_all([warm, hot])
    await db_session.commit()
    await _call(db_session, test_user, test_property, lead=warm)
    test_call = await _call(db_session, test_user, test_property, lead=hot, caller=BROWSER_TEST_CALLER_NUMBER)
    hot.call_session_id = test_call.id
    await db_session.commit()
    db_session.add(_booking(test_property, today_ist(), today_ist() + timedelta(days=1), price=5000))
    await db_session.commit()

    today = today_ist()
    real = await _overview(client, auth_headers, today, today)
    with_tests = await _overview(client, auth_headers, today, today, include_test_calls="true")

    assert real["opportunities"] == {"booking_opportunities": 1, "high_intent": 0, "booking_intent": 0}
    assert with_tests["opportunities"] == {"booking_opportunities": 2, "high_intent": 1, "booking_intent": 1}
    assert real["activity"]["total_calls"] == 1 and with_tests["activity"]["total_calls"] == 2
    # The current-state attention list follows the toggle too.
    assert real["attention"]["high_intent_unconverted"]["count"] == 0
    assert with_tests["attention"]["high_intent_unconverted"]["count"] == 1
    # Financial metrics never depend on the test-call toggle.
    assert real["portfolio"] == with_tests["portfolio"]


async def test_mira_attributed_counts_confirmed_only_and_follows_dates(
    client, auth_headers, db_session, test_property
):
    stay = today_ist() + timedelta(days=10)
    db_session.add_all(
        [
            _booking(test_property, stay, stay + timedelta(days=2), mira_attribution_status="confirmed"),
            _booking(test_property, stay + timedelta(days=3), stay + timedelta(days=4), mira_attribution_status="probable"),
        ]
    )
    await db_session.commit()
    today = today_ist()
    body = await _overview(client, auth_headers, today - timedelta(days=6), today)
    assert body["mira_attributed_bookings"] == 1
    assert body["attention"]["attribution_confirmations"] == 1
    # The bookings were made today, so a past window doesn't include them.
    past = await _overview(client, auth_headers, today - timedelta(days=30), today - timedelta(days=10))
    assert past["mira_attributed_bookings"] == 0


async def test_attention_items_are_current_state_not_date_filtered(
    client, auth_headers, db_session, test_user, test_property
):
    stay = today_ist() + timedelta(days=5)
    db_session.add(_booking(test_property, stay, stay + timedelta(days=2)))  # needs a price
    lead = Lead(user_id=test_user.id, lead_temperature="hot", phone="+919822222222", status="open")
    db_session.add(lead)
    await db_session.commit()
    call = await _call(db_session, test_user, test_property, lead=lead, caller="+919822222222")
    db_session.add(
        PriceEvent(
            user_id=test_user.id,
            property_id=test_property.id,
            call_session_id=call.id,
            price_type="counter_offer",
            source="mira_conversation",
            price=12000,
        )
    )
    await db_session.commit()

    # A window far in the past still shows today's open actions.
    body = await _overview(client, auth_headers, date(2025, 1, 1), date(2025, 1, 31))
    attention = body["attention"]
    assert attention["price_confirmations"] == 1
    assert attention["high_intent_unconverted"] == {"count": 1, "potential_value": 12000.0, "valued_count": 1}


async def test_converted_or_past_high_intent_leads_are_not_attention_items(
    client, auth_headers, db_session, test_user, test_property
):
    booked_via_mira = Lead(user_id=test_user.id, lead_temperature="very_hot", status="open")
    past_stay = Lead(
        user_id=test_user.id, lead_temperature="hot", status="open", check_in=today_ist() - timedelta(days=3)
    )
    closed = Lead(user_id=test_user.id, lead_temperature="hot", status="closed")
    warm = Lead(user_id=test_user.id, lead_temperature="warm", status="open")
    db_session.add_all([booked_via_mira, past_stay, closed, warm])
    await db_session.commit()
    stay = today_ist() + timedelta(days=4)
    db_session.add(
        _booking(
            test_property,
            stay,
            stay + timedelta(days=1),
            price=4000,
            mira_attribution_status="confirmed",
            mira_attribution_lead_id=booked_via_mira.id,
        )
    )
    await db_session.commit()
    body = await _overview(client, auth_headers, today_ist(), today_ist())
    assert body["attention"]["high_intent_unconverted"] == {"count": 0, "potential_value": None, "valued_count": 0}


async def test_overview_validates_range(client, auth_headers):
    resp = await client.get(
        "/api/v1/analytics/overview", params={"start_date": "2026-10-10", "end_date": "2026-10-01"}, headers=auth_headers
    )
    assert resp.status_code == 422


async def test_escalations_follow_the_test_call_toggle(client, auth_headers, db_session, test_user, test_property):
    from app.models.notification import Notification

    test_call = await _call(db_session, test_user, test_property, caller=BROWSER_TEST_CALLER_NUMBER)
    db_session.add(
        Notification(
            property_id=test_property.id, call_session_id=test_call.id, channel="escalation", message="test escalation"
        )
    )
    await db_session.commit()
    today = today_ist()
    real = await _overview(client, auth_headers, today, today)
    with_tests = await _overview(client, auth_headers, today, today, include_test_calls="true")
    assert real["activity"]["escalated_calls"] == 0
    assert with_tests["activity"]["escalated_calls"] == 1


async def test_reporting_days_are_ist_calendar_days(client, auth_headers, db_session, test_user, test_property):
    """A call at 00:30 IST on Oct 18 is 19:00 UTC on Oct 17 -- it belongs to
    the host's Oct 18, not Oct 17."""
    from datetime import datetime

    from app.utils.dates import IST

    call = await _call(db_session, test_user, test_property)
    call.created_at = datetime(2026, 10, 18, 0, 30, tzinfo=IST)
    await db_session.commit()
    oct_17 = await _overview(client, auth_headers, date(2026, 10, 17), date(2026, 10, 17))
    oct_18 = await _overview(client, auth_headers, date(2026, 10, 18), date(2026, 10, 18))
    assert oct_17["activity"]["total_calls"] == 0
    assert oct_18["activity"]["total_calls"] == 1
