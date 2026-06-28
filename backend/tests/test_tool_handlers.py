from datetime import date, timedelta

from app.models.technician import Technician
from app.schemas.tool import (
    CheckCalendarArgs,
    DispatchTechnicianArgs,
    EscalateToHostArgs,
    GetPricingArgs,
    NegotiateRateArgs,
    SendWhatsappArgs,
)
from app.services import lead_service, tool_handlers
from app.services.notification_service import list_notifications


async def test_check_calendar_available(test_property, db_session):
    today = date.today()
    args = CheckCalendarArgs(
        property_id=str(test_property.id), check_in=today + timedelta(days=10), check_out=today + timedelta(days=12)
    )
    result = await tool_handlers.handle_check_calendar(db_session, args)
    assert "AVAILABLE" in result
    assert test_property.name in result


async def test_check_calendar_unknown_property(db_session):
    args = CheckCalendarArgs(property_id="00000000-0000-0000-0000-000000000000", check_in=date.today(), check_out=date.today() + timedelta(days=1))
    result = await tool_handlers.handle_check_calendar(db_session, args)
    assert "couldn't find" in result.lower()


async def test_check_calendar_exceeds_max_guests(test_property, db_session):
    today = date.today()
    args = CheckCalendarArgs(
        property_id=str(test_property.id),
        check_in=today + timedelta(days=10),
        check_out=today + timedelta(days=12),
        num_guests=test_property.max_guests + 5,
    )
    result = await tool_handlers.handle_check_calendar(db_session, args)
    assert "sleeps up to" in result


async def test_get_pricing_includes_total(test_property, db_session):
    today = date.today()
    args = GetPricingArgs(
        property_id=str(test_property.id), check_in=today + timedelta(days=1), check_out=today + timedelta(days=3), num_guests=2
    )
    result = await tool_handlers.handle_get_pricing(db_session, args)
    assert "TOTAL" in result
    assert test_property.name in result


async def test_dispatch_technician_finds_specialist(test_property, db_session):
    db_session.add(
        Technician(property_id=test_property.id, name="Plumber Joe", specialty="plumbing", phone="+911234567890", rating=4.9)
    )
    await db_session.commit()

    args = DispatchTechnicianArgs(property_id=str(test_property.id), issue_type="plumbing", urgency="high")
    result = await tool_handlers.handle_dispatch_technician(db_session, args, call_session_id=None)
    assert "Plumber Joe" in result

    notifications = await list_notifications(db_session)
    assert any("Plumber Joe" in n.message for n in notifications)


async def test_dispatch_technician_no_technician_on_file(test_property, db_session):
    args = DispatchTechnicianArgs(property_id=str(test_property.id), issue_type="lock", urgency="emergency")
    result = await tool_handlers.handle_dispatch_technician(db_session, args, call_session_id=None)
    assert "don't have" in result.lower()


async def test_send_whatsapp_creates_notification(test_property, db_session):
    args = SendWhatsappArgs(phone="+919999999999", message="Hello guest")
    result = await tool_handlers.handle_send_whatsapp(db_session, args, property_id=test_property.id, call_session_id=None)
    assert "queued" in result.lower()

    notifications = await list_notifications(db_session)
    assert any(n.channel == "whatsapp" and "Hello guest" in n.message for n in notifications)


async def test_escalate_to_host_creates_urgent_notification(test_property, db_session):
    args = EscalateToHostArgs(
        property_id=str(test_property.id),
        reason="No water",
        urgency="emergency",
        call_summary="Guest very upset",
        guest_phone="+919999999999",
    )
    result = await tool_handlers.handle_escalate_to_host(
        db_session, args, call_session_id=None, host_user_id=test_property.user_id
    )
    assert "emergency" in result.lower()

    notifications = await list_notifications(db_session)
    assert any(n.urgency == "emergency" for n in notifications)


async def test_escalate_to_host_also_saves_lead_so_it_isnt_left_empty(test_property, db_session):
    # Regression: escalate_to_host used to only create a notification --
    # the rich call_summary/phone the model had already gathered never made
    # it into the Lead row, so a clearly hot lead stayed empty in the CRM.
    args = EscalateToHostArgs(
        property_id=str(test_property.id),
        reason="Finalize booking",
        urgency="medium",
        call_summary="Guest wants to book for 2 guests, July 3-5, total ₹12,987",
        guest_phone="+919999999999",
    )
    await tool_handlers.handle_escalate_to_host(
        db_session, args, call_session_id=None, host_user_id=test_property.user_id
    )

    leads = await lead_service.list_leads(db_session, test_property.user_id)
    assert len(leads) == 1
    assert leads[0].phone == "+919999999999"
    assert "12,987" in leads[0].conversation_summary
    assert leads[0].escalated is True


async def test_negotiate_rate_returns_message(test_property, db_session):
    today = date.today()
    args = NegotiateRateArgs(
        property_id=str(test_property.id),
        check_in=today + timedelta(days=1),
        check_out=today + timedelta(days=3),
        guest_offer=1,
        guest_loyalty="frequent",
    )
    result = await tool_handlers.handle_negotiate_rate(db_session, args)
    assert test_property.name in result
