"""Analytics page definitions (app/services/analytics_service.py): portfolio
metrics from canonical final_booking_price only, completeness reporting,
portfolio-level occupancy, the booking funnel, Mira impact counting only
CONFIRMED attribution, and the /analytics/dashboard endpoint."""

import uuid
from datetime import date, datetime, timedelta, timezone

from app.models.booking import Booking
from app.models.call_session import CallSession
from app.models.lead import Lead
from app.models.property import Property
from app.models.user import User
from app.services import analytics_service
from app.services.analytics_service import AnalyticsScope
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER
from app.utils.dates import today_ist

PERIOD_START = date(2026, 1, 1)
PERIOD_END = date(2026, 1, 10)  # inclusive: 10 nights per property


async def _second_property(db, user) -> Property:
    property_ = Property(user_id=user.id, name="Palm House", city="Goa", base_price=5000)
    db.add(property_)
    await db.commit()
    await db.refresh(property_)
    return property_


def _booking(property_, check_in, check_out, *, price=None, kind="reservation", **extra) -> Booking:
    fields = dict(
        property_id=property_.id,
        check_in=check_in,
        check_out=check_out,
        platform="airbnb",
        source_uid=uuid.uuid4().hex,
        status="confirmed",
        kind=kind,
        price_status="pending_confirmation" if kind == "reservation" else "unknown",
    )
    if price is not None:
        fields.update(final_booking_price=price, price_status="confirmed", price_source="host_entered")
    fields.update(extra)
    return Booking(**fields)


async def _portfolio_fixture(db, user, villa):
    palm = await _second_property(db, user)
    db.add_all(
        [
            # Villa: 3 in-period nights at 9000 total.
            _booking(villa, date(2026, 1, 1), date(2026, 1, 4), price=9000),
            # Villa: 4-night stay, 3 nights (Jan 8, 9, 10) inside the period.
            _booking(villa, date(2026, 1, 8), date(2026, 1, 12), price=8000),
            # Villa: owner block on Jan 5 -- not inventory, not booked.
            _booking(villa, date(2026, 1, 5), date(2026, 1, 6), kind="blocked"),
            # Palm House: 3 nights, final price NOT confirmed yet.
            _booking(palm, date(2026, 1, 2), date(2026, 1, 5)),
        ]
    )
    await db.commit()
    return palm


def _scope(user, properties, *, today=None) -> AnalyticsScope:
    return AnalyticsScope(
        host=user, properties=properties, start=PERIOD_START, end=PERIOD_END, today=today or today_ist()
    )


async def test_portfolio_metrics_use_only_confirmed_final_prices(db_session, test_user, test_property):
    palm = await _portfolio_fixture(db_session, test_user, test_property)
    result = await analytics_service.portfolio_performance(
        db_session, _scope(test_user, [test_property, palm], today=date(2026, 1, 9))
    )

    # Inventory: 2 properties x 10 nights - 1 blocked night = 19.
    assert result["available_nights"] == 19
    assert result["blocked_nights"] == 1
    # Booked: villa 3 + 3, palm 3 = 9.
    assert result["booked_nights"] == 9
    # Portfolio occupancy is 9/19 -- NOT the average of per-property
    # occupancy (villa 6/9, palm 3/10 -> 0.4833).
    assert result["occupancy"] == round(9 / 19, 4)
    # Revenue: 9000 + 8000 * 3/4 (only in-period nights). The unpriced Palm
    # House stay contributes nothing -- never an inferred value.
    assert result["revenue"] == 15000.0
    # ADR over the PRICED stays' nights only: 15000 / 6.
    assert result["adr"] == 2500.0
    # RevPAR over all available nights.
    assert result["revpar"] == round(15000 / 19, 2)
    assert result["completeness"] == {
        "bookings_total": 3,
        "bookings_priced": 2,
        "bookings_missing_price": 1,
        "is_complete": False,
    }


async def test_upcoming_revenue_counts_future_nights_of_priced_bookings(db_session, test_user, test_property):
    palm = await _portfolio_fixture(db_session, test_user, test_property)
    db_session.add(_booking(palm, date(2026, 1, 20), date(2026, 1, 22)))  # future, unpriced
    await db_session.commit()
    result = await analytics_service.portfolio_performance(
        db_session, _scope(test_user, [test_property, palm], today=date(2026, 1, 9))
    )
    # Only the 8000 stay is still running on/after Jan 9: nights 9, 10, 11 of
    # its 4 -> 6000. The future unpriced stay is reported as missing.
    assert result["upcoming_revenue"] == 6000.0
    assert result["upcoming_completeness"]["bookings_total"] == 2
    assert result["upcoming_completeness"]["bookings_missing_price"] == 1


async def test_no_priced_bookings_means_no_revenue_not_zero(db_session, test_user, test_property):
    db_session.add(_booking(test_property, date(2026, 1, 2), date(2026, 1, 4)))
    await db_session.commit()
    result = await analytics_service.portfolio_performance(db_session, _scope(test_user, [test_property]))
    assert result["revenue"] is None
    assert result["adr"] is None
    assert result["revpar"] is None
    assert result["occupancy"] == round(2 / 10, 4)
    assert result["completeness"]["bookings_missing_price"] == 1


async def test_negotiated_price_is_never_used_as_revenue(db_session, test_user, test_property):
    db_session.add(
        _booking(test_property, date(2026, 1, 2), date(2026, 1, 4), initial_price=10000, negotiated_price=9000)
    )
    await db_session.commit()
    result = await analytics_service.portfolio_performance(db_session, _scope(test_user, [test_property]))
    assert result["revenue"] is None


async def test_property_filter_scopes_portfolio(db_session, test_user, test_property):
    palm = await _portfolio_fixture(db_session, test_user, test_property)
    result = await analytics_service.portfolio_performance(db_session, _scope(test_user, [palm]))
    assert result["available_nights"] == 10
    assert result["booked_nights"] == 3
    assert result["revenue"] is None


async def _call(db, user, property_, *, call_type="BOOKING_LEAD", lead=None, caller="+919811111111", status="completed"):
    call = CallSession(
        user_id=user.id,
        property_id=property_.id,
        caller_number=caller,
        call_type=call_type,
        status=status,
        lead_id=lead.id if lead else None,
    )
    db.add(call)
    await db.commit()
    return call


async def _lead(db, user, temperature, status="open", **extra) -> Lead:
    lead = Lead(user_id=user.id, lead_temperature=temperature, status=status, **extra)
    db.add(lead)
    await db.commit()
    return lead


def _today_scope(user, properties) -> AnalyticsScope:
    today = today_ist()
    return AnalyticsScope(host=user, properties=properties, start=today - timedelta(days=1), end=today + timedelta(days=1))


async def test_booking_funnel_uses_persisted_stages(db_session, test_user, test_property):
    warm = await _lead(db_session, test_user, "warm")
    hot = await _lead(db_session, test_user, "hot")
    booked = await _lead(db_session, test_user, "very_hot", status="booked")
    await _call(db_session, test_user, test_property, lead=warm)
    await _call(db_session, test_user, test_property, lead=hot)
    await _call(db_session, test_user, test_property, lead=booked)
    await _call(db_session, test_user, test_property, call_type="GENERAL_QUERY")
    await _call(db_session, test_user, test_property, caller=BROWSER_TEST_CALLER_NUMBER)  # excluded
    await _call(db_session, test_user, test_property, call_type="MISSED_AGENT_BUSY")  # never a conversation

    scope = _today_scope(test_user, [test_property])
    conversations = await analytics_service._load_conversations(db_session, scope, all_properties=True)
    stages = {s["key"]: s for s in analytics_service.booking_funnel(conversations)["stages"]}
    assert [stages[k]["value"] for k in stages] == [4, 3, 3, 2, 1, 1]
    assert stages["booking_related"]["rate_from_previous"] == 0.75
    assert stages["all_conversations"]["rate_from_previous"] is None


async def test_escalated_booking_call_still_counts_as_booking_related(db_session, test_user, test_property):
    call = await _call(db_session, test_user, test_property, call_type="ESCALATED_NO_TRANSFER")
    call.ai_summary = {"booking_snapshot": {"intent": "New Booking"}}
    await db_session.commit()
    scope = _today_scope(test_user, [test_property])
    conversations = await analytics_service._load_conversations(db_session, scope, all_properties=True)
    stages = {s["key"]: s["value"] for s in analytics_service.booking_funnel(conversations)["stages"]}
    assert stages["booking_related"] == 1


async def test_probable_attribution_is_never_counted_as_mira_impact(db_session, test_user, test_property):
    lead = await _lead(db_session, test_user, "very_hot")
    stay = today_ist() + timedelta(days=10)
    db_session.add_all(
        [
            _booking(
                test_property,
                stay,
                stay + timedelta(days=2),
                price=10000,
                mira_attribution_status="confirmed",
                mira_attribution_lead_id=lead.id,
            ),
            _booking(
                test_property,
                stay + timedelta(days=5),
                stay + timedelta(days=7),
                price=5000,
                mira_attribution_status="probable",
                mira_attribution_lead_id=lead.id,
            ),
            _booking(
                test_property,
                stay + timedelta(days=9),
                stay + timedelta(days=11),
                mira_attribution_status="confirmed",
                mira_attribution_lead_id=lead.id,
            ),
        ]
    )
    await db_session.commit()
    scope = _today_scope(test_user, [test_property])
    conversations = await analytics_service._load_conversations(db_session, scope, all_properties=True)
    impact = await analytics_service.mira_impact(db_session, scope, conversations)
    assert impact["attributed_bookings"] == 2
    assert impact["attributed_revenue"] == 10000.0
    assert impact["attributed_completeness"]["bookings_missing_price"] == 1
    assert impact["awaiting_confirmation"] == 1


async def test_after_hours_requires_configured_host_hours(db_session, test_user, test_property):
    await _call(db_session, test_user, test_property)
    scope = _today_scope(test_user, [test_property])
    conversations = await analytics_service._load_conversations(db_session, scope, all_properties=True)
    impact = await analytics_service.mira_impact(db_session, scope, conversations)
    assert impact["after_hours_opportunities"] == {"configured": False, "value": None, "share": None}


async def test_after_hours_counts_booking_calls_outside_host_hours(db_session, test_user, test_property):
    test_user.host_call_hours_enabled = True
    test_user.host_call_hours_start = "09:00"
    test_user.host_call_hours_end = "18:00"
    test_user.host_call_hours_timezone = "Asia/Kolkata"
    await db_session.commit()
    night = await _call(db_session, test_user, test_property)
    day = await _call(db_session, test_user, test_property)
    today = today_ist()
    # 23:30 IST and 12:00 IST, expressed in UTC.
    night.started_at = datetime(today.year, today.month, today.day, 18, 0, tzinfo=timezone.utc)
    day.started_at = datetime(today.year, today.month, today.day, 6, 30, tzinfo=timezone.utc)
    await db_session.commit()
    scope = _today_scope(test_user, [test_property])
    conversations = await analytics_service._load_conversations(db_session, scope, all_properties=True)
    impact = await analytics_service.mira_impact(db_session, scope, conversations)
    assert impact["after_hours_opportunities"] == {"configured": True, "value": 1, "share": 0.5}


async def test_guest_intent_categories(db_session, test_user, test_property):
    lead = await _lead(
        db_session,
        test_user,
        "warm",
        questions_asked=["Is there parking?", "What's the cancellation policy?", "How far is the beach?"],
    )
    call = await _call(db_session, test_user, test_property, lead=lead)
    call.ai_summary = {"objection_tags": ["PRICE_TOO_HIGH"]}
    await _call(db_session, test_user, test_property)  # no structured intent data
    await db_session.commit()
    scope = _today_scope(test_user, [test_property])
    conversations = await analytics_service._load_conversations(db_session, scope, all_properties=True)
    intent = await analytics_service.guest_intent(db_session, conversations)
    counts = {c["key"]: c["count"] for c in intent["categories"]}
    assert intent["conversations_with_data"] == 1
    assert counts["amenities"] == counts["policies"] == counts["location"] == counts["pricing"] == 1
    assert counts["availability"] == 0


async def test_pricing_metrics_hide_values_below_minimum_sample(db_session, test_user, test_property):
    scope = _today_scope(test_user, [test_property])
    conversations = await analytics_service._load_conversations(db_session, scope, all_properties=True)
    pricing = await analytics_service.pricing_and_negotiation(db_session, scope, conversations)
    assert pricing["negotiation_conversion"] == {
        "value": None,
        "sample_size": 0,
        "sufficient": False,
        "min_sample_size": analytics_service.MIN_SAMPLE_SIZE,
    }
    assert pricing["avg_discount"]["value"] is None


async def test_dashboard_endpoint(client, auth_headers, db_session, test_user, test_property):
    await _portfolio_fixture(db_session, test_user, test_property)
    resp = await client.get(
        "/api/v1/analytics/dashboard",
        params={"start_date": PERIOD_START.isoformat(), "end_date": PERIOD_END.isoformat()},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {"portfolio", "funnel", "impact", "guest_intent", "pricing", "needs_confirmation"}
    assert body["portfolio"]["revenue"] == 15000.0
    assert body["portfolio"]["comparison"]["start_date"] == "2025-12-22"
    assert body["portfolio"]["comparison"]["change"]["revenue"] is None  # nothing to compare against


async def test_dashboard_property_filter_and_validation(client, auth_headers, db_session, test_user, test_property):
    palm = await _portfolio_fixture(db_session, test_user, test_property)
    params = {"start_date": PERIOD_START.isoformat(), "end_date": PERIOD_END.isoformat()}
    resp = await client.get(
        "/api/v1/analytics/dashboard", params={**params, "property_id": str(palm.id)}, headers=auth_headers
    )
    assert resp.json()["portfolio"]["booked_nights"] == 3

    other = User(email="someone@example.com", clerk_user_id="user_someone", name="Someone")
    db_session.add(other)
    await db_session.commit()
    foreign = Property(user_id=other.id, name="Not yours", base_price=1)
    db_session.add(foreign)
    await db_session.commit()
    resp = await client.get(
        "/api/v1/analytics/dashboard", params={**params, "property_id": str(foreign.id)}, headers=auth_headers
    )
    assert resp.status_code == 404

    resp = await client.get(
        "/api/v1/analytics/dashboard",
        params={"start_date": PERIOD_END.isoformat(), "end_date": PERIOD_START.isoformat()},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_dashboard_empty_state(client, auth_headers):
    resp = await client.get("/api/v1/analytics/dashboard", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["portfolio"]["occupancy"] is None
    assert body["portfolio"]["revenue"] is None
    assert body["funnel"]["stages"][0]["value"] == 0
    assert body["needs_confirmation"]["total"] == 0


def test_intent_keywords_match_whole_words_not_fragments():
    categorise = analytics_service._question_categories
    assert categorise("Do you have separate bedrooms for the kids?") == {"group"}
    assert categorise("Is the carpet clean?") == set()
    assert categorise("Can you accommodate 6 people?") == {"group"}
    assert categorise("Is there AC in every room?") == {"amenities"}
    assert categorise("What are your rates for Diwali?") == {"pricing"}
    assert categorise("Are pets allowed?") == {"policies"}
    assert categorise("Is it available next weekend?") == {"availability"}
