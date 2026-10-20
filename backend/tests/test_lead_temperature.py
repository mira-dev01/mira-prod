"""Lead temperature semantics (app/services/lead_temperature.py): cold /
warm / hot / very_hot, raise-only merging, and the deterministic floors the
pricing/availability tools apply on top of the LLM's own update_lead value."""

from datetime import timedelta

from sqlalchemy import select

from app.models.lead import Lead
from app.schemas.tool import CheckCalendarArgs, GetPricingArgs, NegotiateRateArgs, UpdateLeadArgs
from app.services import lead_service, tool_handlers
from app.services.lead_temperature import merge_temperature, qualification_floor
from app.utils.dates import today_ist


async def _lead_for(db_session, call_session_id) -> Lead:
    db_session.expire_all()
    return await db_session.scalar(select(Lead).where(Lead.call_session_id == call_session_id))


def test_merge_temperature_only_raises():
    assert merge_temperature(None, "cold") == "cold"
    assert merge_temperature("cold", "hot") == "hot"
    assert merge_temperature("hot", "warm") == "hot"
    assert merge_temperature("hot", "very_hot") == "very_hot"
    assert merge_temperature("very_hot", "cold") == "very_hot"
    assert merge_temperature("warm", None) == "warm"
    assert merge_temperature("warm", "lukewarm") == "warm"


def test_dates_and_guest_count_are_qualification_not_hot():
    floor = qualification_floor(has_dates_or_stay_length=True, num_guests=4, properties_discussed=[])
    assert floor == "warm"
    # One signal on its own isn't qualification yet.
    assert qualification_floor(has_dates_or_stay_length=True, num_guests=None, properties_discussed=[]) is None


async def test_cold_general_enquiry_stays_cold(db_session, test_user, test_call_session):
    await tool_handlers.handle_update_lead(
        db_session,
        UpdateLeadArgs(guest_name="Asha", questions_asked=["Do you have properties in Goa?"], lead_temperature="cold"),
        test_user.id,
        test_call_session.id,
    )
    lead = await _lead_for(db_session, test_call_session.id)
    assert lead.lead_temperature == "cold"


async def test_warm_qualified_enquiry_from_dates_and_guest_count(db_session, test_user, test_call_session):
    check_in = today_ist() + timedelta(days=20)
    # The LLM said "cold", but dates + guest count are two qualification
    # signals: the floor lifts it to warm, not to hot.
    await tool_handlers.handle_update_lead(
        db_session,
        UpdateLeadArgs(check_in=check_in, check_out=check_in + timedelta(days=2), num_guests=4, lead_temperature="cold"),
        test_user.id,
        test_call_session.id,
    )
    lead = await _lead_for(db_session, test_call_session.id)
    assert lead.lead_temperature == "warm"


async def test_stay_length_counts_as_a_qualification_signal(db_session, test_user, test_call_session):
    await tool_handlers.handle_update_lead(
        db_session, UpdateLeadArgs(nights=3, num_guests=2), test_user.id, test_call_session.id
    )
    lead = await _lead_for(db_session, test_call_session.id)
    assert lead.lead_temperature == "warm"


async def test_hot_when_guest_asks_for_pricing(db_session, test_user, test_property, test_call_session):
    check_in = today_ist() + timedelta(days=20)
    await tool_handlers.handle_get_pricing(
        db_session,
        GetPricingArgs(
            property_id=str(test_property.id), check_in=check_in, check_out=check_in + timedelta(days=2), num_guests=2
        ),
        host_user_id=test_user.id,
        call_session_id=test_call_session.id,
    )
    lead = await _lead_for(db_session, test_call_session.id)
    assert lead.lead_temperature == "hot"


async def test_hot_when_guest_negotiates(db_session, test_user, test_property, test_call_session):
    check_in = today_ist() + timedelta(days=20)
    await tool_handlers.handle_negotiate_rate(
        db_session,
        NegotiateRateArgs(
            property_id=str(test_property.id),
            check_in=check_in,
            check_out=check_in + timedelta(days=2),
            guest_offer=1000,
            guest_loyalty="new",
        ),
        host_user_id=test_user.id,
        call_session_id=test_call_session.id,
    )
    lead = await _lead_for(db_session, test_call_session.id)
    assert lead.lead_temperature == "hot"


async def test_availability_check_alone_is_warm_but_confirmed_availability_is_hot(
    db_session, test_user, test_property, test_call_session
):
    check_in = today_ist() + timedelta(days=20)
    await tool_handlers.handle_check_calendar(
        db_session,
        CheckCalendarArgs(property_id=str(test_property.id), check_in=check_in, check_out=check_in + timedelta(days=2)),
        host_user_id=test_user.id,
        call_session_id=test_call_session.id,
    )
    lead = await _lead_for(db_session, test_call_session.id)
    # No bookings, so the dates come back available: "availability confirmed".
    assert lead.lead_temperature == "hot"


async def test_very_hot_explicit_booking_intent(db_session, test_user, test_property, test_call_session):
    check_in = today_ist() + timedelta(days=20)
    await tool_handlers.handle_get_pricing(
        db_session,
        GetPricingArgs(
            property_id=str(test_property.id), check_in=check_in, check_out=check_in + timedelta(days=2), num_guests=2
        ),
        host_user_id=test_user.id,
        call_session_id=test_call_session.id,
    )
    await tool_handlers.handle_update_lead(
        db_session, UpdateLeadArgs(lead_temperature="very_hot"), test_user.id, test_call_session.id
    )
    lead = await _lead_for(db_session, test_call_session.id)
    assert lead.lead_temperature == "very_hot"


async def test_llm_cannot_downgrade_a_hot_lead(db_session, test_user, test_call_session):
    await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, lead_temperature="hot")
    await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, lead_temperature="cold")
    lead = await _lead_for(db_session, test_call_session.id)
    assert lead.lead_temperature == "hot"


async def test_host_can_still_set_any_temperature(client, auth_headers, db_session, test_user, test_call_session):
    lead = await lead_service.upsert_lead(db_session, test_user.id, test_call_session.id, lead_temperature="very_hot")
    resp = await client.patch(f"/api/v1/leads/{lead.id}", json={"lead_temperature": "warm"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["lead_temperature"] == "warm"
