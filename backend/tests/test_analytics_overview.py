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


# ---------------------------------------------------------------------------
# Overview cards added for the redesign: funnel, leads, open actions,
# resolution rate, and the per-card info text.
# ---------------------------------------------------------------------------


async def test_overview_returns_all_six_funnel_stages(client, auth_headers, test_property):
    today = today_ist()
    body = await _overview(client, auth_headers, today, today)
    assert [stage["key"] for stage in body["funnel"]] == [
        "all_conversations",
        "booking_related",
        "qualified_leads",
        "hot_leads",
        "booking_intent",
        "converted",
    ]


async def test_leads_summary_counts_open_and_hot_and_follows_test_toggle(
    client, auth_headers, db_session, test_user, test_property
):
    hot_open = Lead(user_id=test_user.id, lead_temperature="hot", status="open", phone="+919811111111")
    warm_booked = Lead(user_id=test_user.id, lead_temperature="warm", status="booked", phone="+919822222222")
    test_lead = Lead(user_id=test_user.id, lead_temperature="very_hot", status="open", phone=None)
    db_session.add_all([hot_open, warm_booked, test_lead])
    await db_session.commit()
    await _call(db_session, test_user, test_property, lead=test_lead, caller=BROWSER_TEST_CALLER_NUMBER)

    today = today_ist()
    real = await _overview(client, auth_headers, today, today)
    with_tests = await _overview(client, auth_headers, today, today, include_test_calls="true")
    assert real["leads"] == {"total": 2, "open": 1, "hot": 1}
    assert with_tests["leads"] == {"total": 3, "open": 2, "hot": 2}
    # Leads follow the date range: a window in the past has none.
    past = await _overview(client, auth_headers, date(2025, 1, 1), date(2025, 1, 31))
    assert past["leads"] == {"total": 0, "open": 0, "hot": 0}


async def test_open_actions_are_current_state_and_belong_to_this_host_only(
    client, auth_headers, db_session, test_user, test_property
):
    from app.models.ticket import Ticket
    from app.models.user import User

    other = User(email=f"other-{uuid.uuid4().hex[:8]}@example.com", clerk_user_id=f"user_{uuid.uuid4().hex[:16]}", name="Other")
    db_session.add(other)
    await db_session.commit()
    db_session.add_all(
        [
            Ticket(user_id=test_user.id, kind="escalation", summary="Call the guest back", status="open"),
            Ticket(user_id=test_user.id, kind="payment", summary="Payment link failed", status="open"),
            Ticket(user_id=test_user.id, kind="photos", summary="Send photos", status="done"),
            Ticket(user_id=other.id, kind="escalation", summary="Someone else's", status="open"),
        ]
    )
    await db_session.commit()

    body = await _overview(client, auth_headers, date(2025, 1, 1), date(2025, 1, 31))  # far-past window
    assert body["actions"] == {"open": 2, "escalations": 1}


async def test_resolution_rate_counts_completed_follow_ups_as_resolved(
    client, auth_headers, db_session, test_user, test_property
):
    from app.models.notification import Notification
    from app.models.ticket import Ticket
    from app.models.unanswered_question import UnansweredQuestion

    clean = await _call(db_session, test_user, test_property)
    ticket_done = await _call(db_session, test_user, test_property)
    ticket_open = await _call(db_session, test_user, test_property)
    question_pending = await _call(db_session, test_user, test_property)
    question_answered = await _call(db_session, test_user, test_property)
    escalated_closed = await _call(db_session, test_user, test_property)
    escalated_no_ticket = await _call(db_session, test_user, test_property)
    in_progress = await _call(db_session, test_user, test_property)
    in_progress.status = "in_progress"
    db_session.add_all(
        [
            Ticket(user_id=test_user.id, call_session_id=ticket_done.id, kind="photos", summary="Send photos", status="done"),
            Ticket(user_id=test_user.id, call_session_id=ticket_open.id, kind="promise", summary="Follow up", status="open"),
            UnansweredQuestion(
                user_id=test_user.id, call_session_id=question_pending.id, question="Is there a pool?",
                normalized_question="is there a pool?", status="pending",
            ),
            UnansweredQuestion(
                user_id=test_user.id, call_session_id=question_answered.id, question="Is there parking?",
                normalized_question="is there parking?", status="answered",
            ),
            Ticket(user_id=test_user.id, call_session_id=escalated_closed.id, kind="escalation", summary="Escalated", status="dismissed"),
            Notification(property_id=test_property.id, call_session_id=escalated_closed.id, channel="escalation", message="e1"),
            Notification(property_id=test_property.id, call_session_id=escalated_no_ticket.id, channel="escalation", message="e2"),
        ]
    )
    await db_session.commit()
    assert clean.id and in_progress.id  # created above

    today = today_ist()
    body = await _overview(client, auth_headers, today, today)
    # 7 finished conversations (the in-progress one is not counted).
    # Unresolved: open ticket, pending question, escalation with no ticket.
    assert body["resolution"] == {
        "value": round(4 / 7, 3),
        "resolved": 4,
        "total": 7,
        "resolved_clean": 1,  # the call with nothing attached
        "resolved_after_follow_up": 3,  # done ticket, answered question, closed escalation
        "unresolved": 3,
    }


async def test_resolution_rate_is_unavailable_without_conversations(client, auth_headers, test_property):
    today = today_ist()
    body = await _overview(client, auth_headers, today, today)
    assert body["resolution"]["value"] is None and body["resolution"]["total"] == 0


async def test_resolution_ignores_another_hosts_tickets(client, auth_headers, db_session, test_user, test_property):
    from app.models.ticket import Ticket
    from app.models.user import User

    other = User(email=f"other-{uuid.uuid4().hex[:8]}@example.com", clerk_user_id=f"user_{uuid.uuid4().hex[:16]}", name="Other")
    db_session.add(other)
    await db_session.commit()
    call = await _call(db_session, test_user, test_property)
    # A ticket carrying this host's call id but another host's user id must not count against it.
    db_session.add(Ticket(user_id=other.id, call_session_id=call.id, kind="promise", summary="x", status="open"))
    await db_session.commit()
    today = today_ist()
    body = await _overview(client, auth_headers, today, today)
    assert body["resolution"]["unresolved"] == 0 and body["resolution"]["resolved"] == 1


async def test_every_overview_card_has_info_with_a_source(client, auth_headers, test_property):
    today = today_ist()
    body = await _overview(client, auth_headers, today, today)
    for key in (
        "revenue", "upcoming_revenue", "occupancy", "adr", "leads", "open_actions", "resolution_rate",
        "booking_funnel", "recent_calls", "needs_attention", "unanswered_questions",
    ):
        info = body["info"][key]
        assert info["label"] and info["definition"] and info["formula"] and info["source"], key
        assert info["window"] in ("period", "now"), key
