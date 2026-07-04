from datetime import date, timedelta

from app.models.technician import Technician
from app.services.notification_service import list_notifications
from app.voice.tools import build_voice_tools


class _FakeFunctionCallParams:
    """Minimal stand-in for pipecat's FunctionCallParams -- just records the result."""

    def __init__(self):
        self.result = None

    async def result_callback(self, result):
        self.result = result


async def test_check_calendar_available(test_property, db_session, test_user):
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id)
    check_calendar = next(t for t in tools if t.__name__ == "check_calendar")

    today = date.today()
    params = _FakeFunctionCallParams()
    await check_calendar(
        params,
        property_id=str(test_property.id),
        check_in=(today + timedelta(days=10)).isoformat(),
        check_out=(today + timedelta(days=12)).isoformat(),
    )
    assert "AVAILABLE" in params.result
    assert test_property.name in params.result


async def test_check_calendar_invalid_args_returns_graceful_message(db_session, test_user):
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id)
    check_calendar = next(t for t in tools if t.__name__ == "check_calendar")

    params = _FakeFunctionCallParams()
    await check_calendar(params, property_id="not-a-uuid", check_in="not-a-date", check_out="also-not-a-date")
    assert "repeat the dates" in params.result.lower()


async def test_get_pricing_includes_total(test_property, db_session, test_user):
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id)
    get_pricing = next(t for t in tools if t.__name__ == "get_pricing")

    today = date.today()
    params = _FakeFunctionCallParams()
    await get_pricing(
        params,
        property_id=str(test_property.id),
        check_in=(today + timedelta(days=1)).isoformat(),
        check_out=(today + timedelta(days=3)).isoformat(),
        num_guests=2,
    )
    assert "total" in params.result.lower()


async def test_dispatch_technician_uses_bound_call_session_id(test_property, db_session, test_call_session, test_user):
    db_session.add(
        Technician(
            property_id=test_property.id, name="Plumber Joe", specialty="plumbing", phone="+911234567890", rating=4.9
        )
    )
    await db_session.commit()

    tools = build_voice_tools(
        call_session_id=test_call_session.id, property_id=test_property.id, host_user_id=test_user.id
    )
    dispatch_technician = next(t for t in tools if t.__name__ == "dispatch_technician")

    params = _FakeFunctionCallParams()
    await dispatch_technician(params, property_id=str(test_property.id), issue_type="plumbing", urgency="high")
    assert "Plumber Joe" in params.result

    notifications = await list_notifications(db_session)
    matching = [n for n in notifications if "Plumber Joe" in n.message]
    assert matching
    assert matching[0].call_session_id == test_call_session.id


async def test_send_whatsapp_uses_bound_property_id(test_property, db_session, test_user):
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id)
    send_whatsapp = next(t for t in tools if t.__name__ == "send_whatsapp")

    params = _FakeFunctionCallParams()
    await send_whatsapp(params, phone="+919999999999", message="Hello guest")
    assert "queued" in params.result.lower()

    notifications = await list_notifications(db_session)
    matching = [n for n in notifications if n.channel == "whatsapp" and "Hello guest" in n.message]
    assert matching
    assert matching[0].property_id == test_property.id


async def test_recommend_properties_filters_by_host_and_guests(test_property, db_session, test_user):
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    params = _FakeFunctionCallParams()
    await recommend_properties(params, num_guests=2)
    assert test_property.name in params.result
    assert str(test_property.id) in params.result


async def test_recommend_properties_no_match_suggests_escalation(db_session, test_user):
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    params = _FakeFunctionCallParams()
    await recommend_properties(params, num_guests=50)
    assert "host" in params.result.lower()


async def test_update_lead_creates_and_updates_lead_record(test_call_session, db_session, test_user):
    tools = build_voice_tools(call_session_id=test_call_session.id, property_id=None, host_user_id=test_user.id)
    update_lead = next(t for t in tools if t.__name__ == "update_lead")

    params = _FakeFunctionCallParams()
    await update_lead(params, guest_name="Asha", num_guests=4, lead_temperature="hot")
    await update_lead(params, budget=5000, next_follow_up="Call back tomorrow")

    from app.models.lead import Lead
    from sqlalchemy import select

    lead = await db_session.scalar(select(Lead).where(Lead.call_session_id == test_call_session.id))
    assert lead is not None
    assert lead.guest_name == "Asha"
    assert lead.lead_temperature == "hot"
    assert float(lead.budget) == 5000
    assert lead.next_follow_up == "Call back tomorrow"


async def test_search_faq_returns_verified_entry(test_property, db_session, test_user):
    from app.models.faq_entry import FaqEntry

    db_session.add(
        FaqEntry(
            user_id=test_user.id,
            property_id=test_property.id,
            question="What's the wifi password?",
            answer="GuestWifi123",
            status="verified",
        )
    )
    await db_session.commit()

    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id)
    search_faq = next(t for t in tools if t.__name__ == "search_faq")

    params = _FakeFunctionCallParams()
    await search_faq(params, query="wifi password")
    assert "GuestWifi123" in params.result


async def test_search_faq_no_match_signals_escalation(db_session, test_user):
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id)
    search_faq = next(t for t in tools if t.__name__ == "search_faq")

    params = _FakeFunctionCallParams()
    await search_faq(params, query="something nobody answered")
    assert "verified information" in params.result.lower()
