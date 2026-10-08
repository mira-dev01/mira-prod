"""Existing-booking recognition and lookup (guest_booking_service +
lookup_booking), the past-date guard on booking tools, policy-gated loyalty,
and the host-unavailable transfer messaging."""

import uuid
from datetime import timedelta

from sqlalchemy import func, select

from app.models.booking import Booking
from app.models.guest_profile import GuestProfile
from app.models.lead import Lead
from app.models.negotiation_rule import NegotiationRule
from app.models.notification import Notification
from app.models.property import Property
from app.models.user import User
from app.prompts.system_prompt import build_system_prompt
from app.schemas.tool import (
    CheckCalendarArgs,
    GetPricingArgs,
    LookupBookingArgs,
    NegotiateRateArgs,
    RequestHostTransferArgs,
    ToolBusinessError,
    UpdateLeadArgs,
)
from app.services import guest_booking_service, pricing_engine, tool_handlers
from app.utils.dates import today_ist
from app.voice import handoff_signal

TODAY = today_ist()


async def _booked_lead(db, host_id, *, phone=None, guest_name=None, profile=None, start_offset=10, prop="Test Villa"):
    lead = Lead(
        user_id=host_id,
        status="booked",
        phone=phone,
        guest_name=guest_name,
        guest_profile_id=profile.id if profile else None,
        properties_discussed=[prop],
        check_in=TODAY + timedelta(days=start_offset),
        check_out=TODAY + timedelta(days=start_offset + 2),
    )
    db.add(lead)
    await db.commit()
    return lead


async def _other_host(db) -> User:
    user = User(email=f"other-{uuid.uuid4().hex[:8]}@example.com", clerk_user_id=f"user_{uuid.uuid4().hex[:16]}")
    db.add(user)
    await db.commit()
    return user


# --- guest_booking_service.find_bookings -----------------------------------------


async def test_phone_lookup_matches_profile_phone_in_any_format_including_past_stays(test_user, db_session):
    profile = GuestProfile(phone="+91 98765-43210", host_id=test_user.id, name="Priya", total_stays=2)
    db_session.add(profile)
    await db_session.commit()
    await _booked_lead(db_session, test_user.id, profile=profile, start_offset=-20)
    await _booked_lead(db_session, test_user.id, profile=profile, start_offset=15)

    matches = await guest_booking_service.find_bookings(db_session, test_user.id, phone="09876543210")

    assert len(matches) == 2
    assert all(m.matched_by == "phone" for m in matches)
    assert matches[0].check_in > matches[1].check_in  # newest first
    assert matches[1].is_past(TODAY)


async def test_name_lookup_is_case_insensitive(test_user, db_session):
    await _booked_lead(db_session, test_user.id, guest_name="Priya Sharma")

    matches = await guest_booking_service.find_bookings(db_session, test_user.id, name="priya sharma")

    assert len(matches) == 1
    assert matches[0].matched_by == "name"


async def test_lookup_never_returns_another_hosts_bookings(test_user, db_session):
    other = await _other_host(db_session)
    await _booked_lead(db_session, other.id, phone="9876543210", guest_name="Priya")

    assert await guest_booking_service.find_bookings(db_session, test_user.id, phone="9876543210", name="Priya") == []


async def test_lookup_searches_imported_calendar_bookings(test_user, test_property, db_session):
    db_session.add(
        Booking(
            property_id=test_property.id,
            guest_phone="+919876543210",
            guest_name="Rahul",
            check_in=TODAY + timedelta(days=5),
            check_out=TODAY + timedelta(days=7),
        )
    )
    await db_session.commit()

    matches = await guest_booking_service.find_bookings(db_session, test_user.id, phone="9876543210")

    assert [m.property_name for m in matches] == ["Test Villa"]


async def test_property_hint_narrows_matches(test_user, db_session):
    await _booked_lead(db_session, test_user.id, guest_name="Priya", prop="Alpine Ridge Chalet")
    await _booked_lead(db_session, test_user.id, guest_name="Priya", prop="Sea View Villa", start_offset=30)

    matches = await guest_booking_service.find_bookings(
        db_session, test_user.id, name="Priya", property_hint="alpine ridge"
    )

    assert [m.property_name for m in matches] == ["Alpine Ridge Chalet"]


async def test_lookup_never_creates_a_guest_profile(test_user, db_session):
    before = await db_session.scalar(select(func.count()).select_from(GuestProfile))
    await guest_booking_service.find_bookings(db_session, test_user.id, phone="9000000000", name="Nobody")
    assert await db_session.scalar(select(func.count()).select_from(GuestProfile)) == before


async def test_find_active_booking_falls_back_to_calendar_booking_and_skips_past(
    test_user, test_property, db_session
):
    for offset in (-10, 3):
        db_session.add(
            Booking(
                property_id=test_property.id,
                guest_phone="9876543210",
                check_in=TODAY + timedelta(days=offset),
                check_out=TODAY + timedelta(days=offset + 2),
            )
        )
    await db_session.commit()

    active = await guest_booking_service.find_active_booking(db_session, test_user.id, None, "+919876543210")

    assert active is not None
    assert active.check_in == TODAY + timedelta(days=3)


# --- lookup_booking tool ----------------------------------------------------------


async def test_lookup_booking_no_match_hands_off_to_transfer(test_user, db_session):
    result = await tool_handlers.handle_lookup_booking(db_session, LookupBookingArgs(name="Nobody"), test_user.id)
    assert isinstance(result, ToolBusinessError)
    assert "request_host_transfer" in result


async def test_lookup_booking_phone_match_includes_name(test_user, db_session):
    await _booked_lead(db_session, test_user.id, phone="9876543210", guest_name="Priya")

    result = await tool_handlers.handle_lookup_booking(
        db_session, LookupBookingArgs(phone="9876543210"), test_user.id
    )

    assert "Test Villa" in result
    assert "under Priya" in result
    assert "9876543210" not in result


async def test_lookup_booking_name_only_match_shares_property_and_dates_only(test_user, db_session):
    await _booked_lead(db_session, test_user.id, phone="9876543210", guest_name="Priya")

    result = await tool_handlers.handle_lookup_booking(db_session, LookupBookingArgs(name="Priya"), test_user.id)

    assert "Test Villa" in result
    assert "matched by name only" in result
    assert "under Priya" not in result


# --- past-date guard --------------------------------------------------------------


async def test_check_calendar_rejects_past_dates_without_creating_a_lead(test_user, test_property, db_session):
    args = CheckCalendarArgs(
        property_id=str(test_property.id),
        check_in=TODAY - timedelta(days=3),
        check_out=TODAY - timedelta(days=1),
    )
    result = await tool_handlers.handle_check_calendar(db_session, args, test_user.id)

    assert isinstance(result, ToolBusinessError)
    assert "lookup_booking" in result
    assert await db_session.scalar(select(func.count()).select_from(Lead)) == 0


async def test_check_calendar_allows_same_day_check_in(test_user, test_property, db_session):
    args = CheckCalendarArgs(
        property_id=str(test_property.id), check_in=TODAY, check_out=TODAY + timedelta(days=2)
    )
    result = await tool_handlers.handle_check_calendar(db_session, args, test_user.id)
    assert "already passed" not in result


async def test_get_pricing_and_negotiate_rate_reject_past_dates(test_user, test_property, db_session):
    dates = dict(check_in=TODAY - timedelta(days=3), check_out=TODAY - timedelta(days=1))
    pricing = await tool_handlers.handle_get_pricing(
        db_session, GetPricingArgs(property_id=str(test_property.id), num_guests=2, **dates), test_user.id
    )
    negotiation = await tool_handlers.handle_negotiate_rate(
        db_session, NegotiateRateArgs(property_id=str(test_property.id), **dates), test_user.id
    )
    assert isinstance(pricing, ToolBusinessError) and "already passed" in pricing
    assert isinstance(negotiation, ToolBusinessError) and "already passed" in negotiation


async def test_update_lead_keeps_name_but_drops_past_dates(test_user, db_session):
    args = UpdateLeadArgs(
        guest_name="Priya", check_in=TODAY - timedelta(days=5), check_out=TODAY - timedelta(days=3)
    )
    await tool_handlers.handle_update_lead(db_session, args, test_user.id, None)

    lead = await db_session.scalar(select(Lead).where(Lead.user_id == test_user.id))
    assert lead.guest_name == "Priya"
    assert lead.check_in is None and lead.check_out is None


# --- transfer: host unavailable / missed transfer ----------------------------------


async def test_transfer_with_unreachable_host_says_host_unavailable_and_will_follow_up(test_user, db_session):
    result = await tool_handlers.handle_request_host_transfer(
        db_session, RequestHostTransferArgs(reason="booking not found"), None, None, test_user.id
    )
    assert "isn't available to take the call at the moment" in result
    assert "follow up with you shortly" in result


async def test_successful_transfer_leaves_a_follow_up_notification(test_property, test_call_session, db_session):
    test_property.owner.phone = "9876543210"
    await db_session.commit()

    handoff_signal.register_call(test_call_session.id)
    try:
        result = await tool_handlers.handle_request_host_transfer(
            db_session,
            RequestHostTransferArgs(reason="booking not found"),
            test_call_session.id,
            test_property.id,
            test_property.user_id,
        )
    finally:
        handoff_signal.unregister_call(test_call_session.id)

    assert "connecting" in result.lower()
    notification = await db_session.scalar(
        select(Notification).where(Notification.call_session_id == test_call_session.id)
    )
    assert notification is not None and "follow up" in notification.message
    lead = await db_session.scalar(select(Lead).where(Lead.call_session_id == test_call_session.id))
    assert lead.transferred_to_host is True


# --- loyalty only via host policy ----------------------------------------------------


async def test_has_repeat_guest_discount_reflects_approved_policy(test_user, db_session):
    assert await pricing_engine.has_repeat_guest_discount(db_session, test_user.id) is False
    db_session.add(
        NegotiationRule(host_id=test_user.id, rule_type="discount_repeat_guest", discount_percent=8, status="approved")
    )
    await db_session.commit()
    assert await pricing_engine.has_repeat_guest_discount(db_session, test_user.id) is True


async def test_no_policy_means_no_loyalty_bonus(test_user, test_property, db_session):
    check_in = TODAY + timedelta(days=10)
    kwargs = dict(guest_offer=1, host_id=test_user.id)
    new = await pricing_engine.negotiate_rate(
        db_session, test_property, check_in, check_in + timedelta(days=2), guest_loyalty="new", **kwargs
    )
    frequent = await pricing_engine.negotiate_rate(
        db_session, test_property, check_in, check_in + timedelta(days=2), guest_loyalty="frequent", **kwargs
    )
    assert frequent.counter_offer == new.counter_offer


# --- prompt -----------------------------------------------------------------------


def test_prompt_teaches_past_date_question_and_lookup_flow():
    prompt = build_system_prompt(
        Property(id=uuid.uuid4(), user_id=uuid.uuid4(), name="Villa", base_price=4000, max_guests=4),
        None,
        User(id=uuid.uuid4(), email="h@example.com", name="Asha"),
    )
    assert "are you asking about a previous or existing booking" in prompt
    assert "call lookup_booking" in prompt
    assert "host isn't available at the moment and will follow up" in prompt


# --- voice wrappers: past dates never reach ConversationState ---------------------


class _Params:
    async def result_callback(self, result, properties=None):
        self.result = result


def _voice_tool(name, test_user, state, caller_number=None):
    from app.voice.tools import build_voice_tools

    tools = build_voice_tools(
        call_session_id=None,
        property_id=None,
        host_user_id=test_user.id,
        conversation_state=state,
        caller_number=caller_number,
    )
    return next(t for t in tools if t.__name__ == name)


async def test_update_lead_wrapper_skips_past_dates_but_keeps_check_out_only_updates(test_user, db_session):
    from app.voice.conversation_state import ConversationState

    state = ConversationState()
    update_lead = _voice_tool("update_lead", test_user, state)

    await update_lead(_Params(), check_in=(TODAY - timedelta(days=4)).isoformat())
    assert "check_in" not in state.slots

    future_out = (TODAY + timedelta(days=9)).isoformat()
    await update_lead(_Params(), check_out=future_out)
    assert state.slots["check_out"] == future_out


async def test_check_calendar_wrapper_never_stores_past_dates(test_user, test_property, db_session):
    from app.voice.conversation_state import ConversationState

    state = ConversationState()
    check_calendar = _voice_tool("check_calendar", test_user, state)
    params = _Params()
    await check_calendar(
        params,
        property_id=str(test_property.id),
        check_in=(TODAY - timedelta(days=3)).isoformat(),
        check_out=(TODAY - timedelta(days=1)).isoformat(),
    )
    assert params.result["status"] == "business_error"
    assert "check_in" not in state.slots


async def test_lookup_booking_wrapper_searches_the_callers_own_number(test_user, db_session):
    await _booked_lead(db_session, test_user.id, phone="9876543210", guest_name="Priya")
    lookup_booking = _voice_tool("lookup_booking", test_user, None, caller_number="+919876543210")
    params = _Params()

    await lookup_booking(params, name="Someone Else")

    assert "under Priya" in params.result["result"]


async def test_update_lead_with_past_date_tells_the_model_to_ask_about_an_existing_booking(test_user, db_session):
    # Regression 2026-10-08: "2nd October" said on 8 Oct was recorded via
    # update_lead, silently dropped, and answered with a plain "Saved." --
    # the model carried on qualifying it as a new booking.
    result = await tool_handlers.handle_update_lead(
        db_session, UpdateLeadArgs(check_in=TODAY - timedelta(days=6)), test_user.id, None
    )
    assert isinstance(result, ToolBusinessError)
    assert "previous or existing booking" in result


def test_past_date_rule_is_a_golden_rule_in_both_agent_modes():
    from app.prompts.system_prompt import build_lead_system_prompt

    lead_prompt = build_lead_system_prompt(User(id=uuid.uuid4(), email="h@example.com", name="Asha"), [])
    assert "Past dates come before everything else" in lead_prompt
    assert lead_prompt.index("Past dates come before everything else") < lead_prompt.index("Lead qualification workflow")
