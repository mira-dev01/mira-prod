from datetime import date, timedelta

from app.models.technician import Technician
from app.services.notification_service import list_notifications
from app.voice.silence_watchdog import SilenceWatchdogProcessor
from app.voice.tools import _parse_iso_date, build_voice_tools


def test_parse_iso_date_accepts_both_iso_string_and_raw_date_object():
    """Availability-first recommendations, Implementation 2 (self-review
    find): state.slots["check_in"/"check_out"] holds an ISO string from
    update_lead's wrapper, but a raw date object from check_calendar/
    get_pricing/negotiate_rate's wrappers (all three write args.check_in
    straight through, already typed `date` by their own Pydantic schemas).
    date.fromisoformat() only accepts str and raises TypeError -- not
    ValueError -- on a date object, so the pre-fix version of this function
    crashed uncaught on that second, equally real, path."""
    real_date = date(2026, 10, 5)
    assert _parse_iso_date(real_date) == real_date
    assert _parse_iso_date("2026-10-05") == real_date
    assert _parse_iso_date(None) is None
    assert _parse_iso_date("not-a-date") is None
    assert _parse_iso_date("") is None


class _FakeFunctionCallParams:
    """Minimal stand-in for pipecat's FunctionCallParams -- just records the result."""

    def __init__(self):
        self.result = None
        self.properties = None

    async def result_callback(self, result, properties=None):
        self.result = result
        self.properties = properties


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


async def test_send_whatsapp_falls_back_to_caller_number_when_phone_omitted(test_property, db_session, test_user):
    # Feature request 2026-07-21: "send it to the number I'm calling from"
    # should work without the guest reciting digits -- the model can leave
    # phone unset and the caller's own signaling-level number is used.
    tools = build_voice_tools(
        call_session_id=None,
        property_id=test_property.id,
        host_user_id=test_user.id,
        caller_number="+919876543210",
    )
    send_whatsapp = next(t for t in tools if t.__name__ == "send_whatsapp")

    params = _FakeFunctionCallParams()
    await send_whatsapp(params, message="Here are the details")
    assert "queued" in params.result.lower()

    notifications = await list_notifications(db_session)
    matching = [n for n in notifications if n.channel == "whatsapp" and "9876543210" in n.message]
    assert matching


async def test_send_whatsapp_explicit_phone_wins_over_caller_number(test_property, db_session, test_user):
    # A guest booking on behalf of someone else can still name a different number.
    tools = build_voice_tools(
        call_session_id=None,
        property_id=test_property.id,
        host_user_id=test_user.id,
        caller_number="+919876543210",
    )
    send_whatsapp = next(t for t in tools if t.__name__ == "send_whatsapp")

    params = _FakeFunctionCallParams()
    await send_whatsapp(params, message="Hi", phone="+911111111111")

    notifications = await list_notifications(db_session)
    matching = [n for n in notifications if n.channel == "whatsapp" and "1111111111" in n.message]
    assert matching


async def test_send_whatsapp_no_phone_and_no_caller_number_asks_instead_of_erroring(
    test_property, db_session, test_user
):
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id)
    send_whatsapp = next(t for t in tools if t.__name__ == "send_whatsapp")

    params = _FakeFunctionCallParams()
    await send_whatsapp(params, message="Hi")
    assert "could you share one" in params.result.lower()


async def test_send_photos_falls_back_to_caller_number(test_property, db_session, test_user):
    test_property.photos = ["https://res.cloudinary.com/demo/image/upload/v1/photo.jpg"]
    await db_session.commit()

    tools = build_voice_tools(
        call_session_id=None,
        property_id=test_property.id,
        host_user_id=test_user.id,
        caller_number="+919876543210",
    )
    send_photos = next(t for t in tools if t.__name__ == "send_photos")

    params = _FakeFunctionCallParams()
    await send_photos(params, property_id=str(test_property.id))
    assert "flag" not in params.result.lower()  # didn't hit the "no photos on file" branch

    notifications = await list_notifications(db_session)
    matching = [n for n in notifications if "9876543210" in n.message]
    assert matching


async def test_update_lead_falls_back_to_caller_number_for_phone(test_call_session, db_session, test_user):
    # Mirrors send_whatsapp/send_photos: a guest who never recites their
    # number should still have it captured on the Lead from caller ID.
    tools = build_voice_tools(
        call_session_id=test_call_session.id,
        property_id=None,
        host_user_id=test_user.id,
        caller_number="+919876543210",
    )
    update_lead = next(t for t in tools if t.__name__ == "update_lead")

    params = _FakeFunctionCallParams()
    await update_lead(params, guest_name="Asha")

    from app.models.lead import Lead
    from sqlalchemy import select

    lead = await db_session.scalar(select(Lead).where(Lead.call_session_id == test_call_session.id))
    assert lead.phone == "9876543210"


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


async def test_end_call_arms_the_silence_watchdog(db_session, test_user):
    watchdog = SilenceWatchdogProcessor()
    tools = build_voice_tools(
        call_session_id=None, property_id=None, host_user_id=test_user.id, silence_watchdog=watchdog
    )
    end_call = next(t for t in tools if t.__name__ == "end_call")

    params = _FakeFunctionCallParams()
    await end_call(params)

    # The tool itself never ends the call directly (see silence_watchdog.py --
    # only the next BotStoppedSpeakingFrame, once the agent's closing line has
    # actually finished playing, does that) -- it just arms the flag.
    assert watchdog._end_requested is True
    assert params.result is not None


async def test_end_call_without_a_watchdog_still_returns_a_result(db_session, test_user):
    # Guards against a caller (e.g. a future test or entry point) building
    # tools without threading a watchdog through -- end_call should degrade
    # to a no-op rather than raising.
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id)
    end_call = next(t for t in tools if t.__name__ == "end_call")

    params = _FakeFunctionCallParams()
    await end_call(params)
    assert params.result is not None


async def test_decline_irrelevant_call_arms_the_silence_watchdog(db_session, test_user):
    watchdog = SilenceWatchdogProcessor()
    tools = build_voice_tools(
        call_session_id=None, property_id=None, host_user_id=test_user.id, silence_watchdog=watchdog
    )
    decline_irrelevant_call = next(t for t in tools if t.__name__ == "decline_irrelevant_call")

    params = _FakeFunctionCallParams()
    await decline_irrelevant_call(params)

    # Same graceful-hangup mechanism as end_call -- only arms the flag, the
    # actual hangup happens on the next BotStoppedSpeakingFrame once the
    # agent's decline line has finished playing.
    assert watchdog._end_requested is True
    assert params.result is not None


async def test_decline_irrelevant_call_without_a_watchdog_still_returns_a_result(db_session, test_user):
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id)
    decline_irrelevant_call = next(t for t in tools if t.__name__ == "decline_irrelevant_call")

    params = _FakeFunctionCallParams()
    await decline_irrelevant_call(params)
    assert params.result is not None
