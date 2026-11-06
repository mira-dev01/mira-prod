"""One property, two ways to book it: the entire place or by the room.

Covers the scope vocabulary and recommendation (stay_scope), scope-aware
availability and the two-feed iCal sync (calendar_service), the check_calendar /
get_pricing behaviour including the double-occupancy hold, the dashboard API
validation, and that a property NOT rented by the room is untouched.
"""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
import respx
from httpx import Response
from sqlalchemy import select

from app.models.booking import Booking
from app.models.notification import Notification
from app.models.ticket import Ticket
from app.prompts.system_prompt import build_system_prompt
from app.schemas.tool import CheckCalendarArgs, GetPricingArgs
from app.services import calendar_service, stay_scope, tool_handlers
from app.services.property.card import build_property_card, stay_options_note
from app.services.property.chunking import build_fact_chunks
from tests.conftest import auth_headers_for

IN = date.today() + timedelta(days=20)
OUT = IN + timedelta(days=3)


def _room_property(**overrides):
    values = dict(
        room_rental_enabled=True,
        room_base_price=3500,
        rentable_room_count=4,
        room_max_guests=2,
        room_details="King bed and an attached bathroom.",
        base_price=14000,
        max_guests=10,
        name="Test Villa",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
async def room_property(test_property, db_session):
    test_property.room_rental_enabled = True
    test_property.room_base_price = 3500
    test_property.rentable_room_count = 4
    test_property.room_max_guests = 2
    test_property.room_details = "King bed and an attached bathroom."
    test_property.base_price = 14000
    test_property.max_guests = 10
    await db_session.commit()
    await db_session.refresh(test_property)
    return test_property


def _booking(property_, scope="entire_place", rooms=None, kind="reservation", source_uid=None, check_in=IN, check_out=OUT):
    return Booking(
        property_id=property_.id,
        check_in=check_in,
        check_out=check_out,
        status="confirmed",
        kind=kind,
        stay_scope=scope,
        rooms_booked=rooms,
        source_uid=source_uid,
        guest_name="Someone",
    )


# -- which way suits the guest ----------------------------------------------


def test_party_wants_the_entire_place_whatever_the_head_count():
    pick = stay_scope.recommend_scope(_room_property(), 2, "birthday party")
    assert pick.scope == stay_scope.ENTIRE_PLACE


def test_small_night_stay_wants_rooms_sized_to_the_group():
    pick = stay_scope.recommend_scope(_room_property(), 3, "just a night stay")
    assert (pick.scope, pick.rooms) == (stay_scope.ROOMS, 2)  # 3 guests, 2 per room


def test_group_that_would_need_every_room_gets_the_entire_place():
    # 8 guests / 2 per room = all 4 rooms: the whole place is the better deal.
    assert stay_scope.recommend_scope(_room_property(), 8, None).scope == stay_scope.ENTIRE_PLACE


def test_group_bigger_than_the_rooms_sleep_gets_the_entire_place():
    assert stay_scope.recommend_scope(_room_property(), 9, None).scope == stay_scope.ENTIRE_PLACE


def test_unknown_party_and_occasion_means_ask_not_guess():
    assert stay_scope.recommend_scope(_room_property(), None, None) is None


def test_property_not_rented_by_the_room_has_no_recommendation():
    assert stay_scope.recommend_scope(_room_property(room_rental_enabled=False), 2, "party") is None


def test_half_filled_room_tab_is_not_offered():
    # enabled but no price: the agent could not price a room, so it must not offer one.
    assert not stay_scope.offers_rooms(_room_property(room_base_price=None))
    assert not stay_scope.offers_rooms(_room_property(rentable_room_count=0))


def test_scope_labels():
    assert stay_scope.scope_label("entire_place") == "the entire place"
    assert stay_scope.scope_label("rooms", 1) == "1 room"
    assert stay_scope.scope_label("rooms", 3) == "3 rooms"
    assert stay_scope.booking_scope_label(SimpleNamespace(stay_scope="rooms", rooms_booked=2)) == "2 rooms"


# -- pricing view -------------------------------------------------------------


def test_entire_place_is_priced_off_the_property_itself():
    prop = _room_property()
    assert stay_scope.priced_property(prop, stay_scope.ENTIRE_PLACE, None) is prop


def test_room_view_prices_at_the_room_rate_and_never_fetches_live_airbnb_price():
    prop = _room_property(exact_airbnb_pricing=True, airbnb_listing_id="123")
    view = stay_scope.priced_property(prop, stay_scope.ROOMS, 2)
    assert view.base_price == 7000
    assert view.exact_airbnb_pricing is False and view.airbnb_listing_id is None
    assert view.name == "Test Villa (2 rooms)"  # negotiation messages say what they price
    assert view.max_guests == 10  # everything else reads through


# -- scope-aware availability ---------------------------------------------------


async def test_is_available_still_treats_any_booking_as_blocking_the_whole_place(room_property, db_session):
    db_session.add(_booking(room_property, "rooms", 1))
    await db_session.commit()
    assert await calendar_service.is_available(db_session, room_property.id, IN, OUT) is False


async def test_one_room_booked_leaves_other_rooms_bookable(room_property, db_session):
    db_session.add(_booking(room_property, "rooms", 1))
    await db_session.commit()
    result = await calendar_service.scope_availability(db_session, room_property, "rooms", IN, OUT, 3)
    assert result.available


async def test_rooms_run_out_when_the_count_is_exceeded_on_any_night(room_property, db_session):
    db_session.add(_booking(room_property, "rooms", 3, check_in=IN + timedelta(days=1), check_out=IN + timedelta(days=2)))
    await db_session.commit()
    ok = await calendar_service.scope_availability(db_session, room_property, "rooms", IN, OUT, 1)
    too_many = await calendar_service.scope_availability(db_session, room_property, "rooms", IN, OUT, 2)
    assert ok.available
    assert (too_many.available, too_many.reason) == (False, "rooms_full")


async def test_entire_place_booking_blocks_rooms(room_property, db_session):
    db_session.add(_booking(room_property, "entire_place"))
    await db_session.commit()
    result = await calendar_service.scope_availability(db_session, room_property, "rooms", IN, OUT, 1)
    assert (result.available, result.reason) == (False, "entire_place_taken")


async def test_room_booking_in_the_way_of_the_entire_place_is_double_occupancy(room_property, db_session):
    db_session.add(_booking(room_property, "rooms", 2))
    await db_session.commit()
    result = await calendar_service.scope_availability(db_session, room_property, "entire_place", IN, OUT)
    assert (result.available, result.reason) == (False, "rooms_taken")
    assert result.conflicts[0].rooms_booked == 2


async def test_an_airbnb_block_on_the_room_feed_is_not_a_guests_room_booking(room_property, db_session):
    # "Not available" on the room listing has no guest behind it: it is not a
    # double occupancy, and it closes every room.
    db_session.add(_booking(room_property, "rooms", 1, kind="blocked", source_uid="blk-1"))
    await db_session.commit()
    entire = await calendar_service.scope_availability(db_session, room_property, "entire_place", IN, OUT)
    rooms = await calendar_service.scope_availability(db_session, room_property, "rooms", IN, OUT, 1)
    assert entire.reason == "entire_place_taken"
    assert (rooms.available, rooms.reason) == (False, "rooms_full")


# -- two calendar feeds, one property -------------------------------------------


def _ics(uid: str, start: str, end: str) -> str:
    return (
        "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Airbnb Inc//Hosting Calendar 0.8.8//EN\n"
        f"BEGIN:VEVENT\nDTSTART:{start}\nDTEND:{end}\nUID:{uid}\nSUMMARY:Reserved\nEND:VEVENT\nEND:VCALENDAR\n"
    )


@respx.mock
async def test_each_feed_only_manages_its_own_bookings(room_property, db_session):
    room_property.ical_url = "https://airbnb.com/calendar/ical/whole.ics"
    room_property.room_ical_url = "https://airbnb.com/calendar/ical/rooms.ics"
    await db_session.commit()
    whole = respx.get(room_property.ical_url).mock(return_value=Response(200, text=_ics("whole-1", "20271001", "20271004")))
    rooms = respx.get(room_property.room_ical_url).mock(return_value=Response(200, text=_ics("room-1", "20271010", "20271012")))

    assert await calendar_service.sync_property_ical(db_session, room_property) == 2
    rows = {b.source_uid: b for b in (await db_session.scalars(select(Booking).where(Booking.property_id == room_property.id))).all()}
    assert (rows["whole-1"].stay_scope, rows["whole-1"].rooms_booked) == ("entire_place", None)
    assert (rows["room-1"].stay_scope, rows["room-1"].rooms_booked) == ("rooms", 1)

    # The room guest cancels on Airbnb: only the room booking goes.
    rooms.mock(return_value=Response(200, text="BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n"))
    await calendar_service.sync_property_ical(db_session, room_property)
    left = (await db_session.scalars(select(Booking).where(Booking.property_id == room_property.id))).all()
    assert [b.source_uid for b in left] == ["whole-1"]
    assert whole.called


@respx.mock
async def test_a_host_corrected_room_count_survives_the_next_sync(room_property, db_session):
    room_property.room_ical_url = "https://airbnb.com/calendar/ical/rooms2.ics"
    await db_session.commit()
    respx.get(room_property.room_ical_url).mock(return_value=Response(200, text=_ics("room-9", "20271020", "20271022")))
    await calendar_service.sync_property_ical(db_session, room_property)
    booking = await db_session.scalar(select(Booking).where(Booking.source_uid == "room-9"))
    booking.rooms_booked = 3
    await db_session.commit()
    await calendar_service.sync_property_ical(db_session, room_property)
    await db_session.refresh(booking)
    assert booking.rooms_booked == 3


# -- check_calendar -----------------------------------------------------------------


def _calendar_args(property_, **kw):
    return CheckCalendarArgs(property_id=str(property_.id), check_in=IN, check_out=OUT, **kw)


async def test_rooms_stay_is_checked_against_rooms(room_property, db_session):
    db_session.add(_booking(room_property, "rooms", 1))
    await db_session.commit()
    result = await tool_handlers.handle_check_calendar(
        db_session, _calendar_args(room_property, stay_scope="rooms", rooms=2, num_guests=4)
    )
    assert "AVAILABLE" in result and "2 rooms" in result


async def test_rooms_request_for_a_property_not_rented_by_the_room_is_declined(test_property, db_session):
    result = await tool_handlers.handle_check_calendar(db_session, _calendar_args(test_property, stay_scope="rooms"))
    assert "only rented as the entire place" in result


async def test_too_many_guests_for_the_rooms_asked_for(room_property, db_session):
    result = await tool_handlers.handle_check_calendar(
        db_session, _calendar_args(room_property, stay_scope="rooms", rooms=1, num_guests=5)
    )
    assert "sleeps up to 2" in result or "up to 2 guests" in result


async def test_entire_place_over_a_room_booking_is_a_temporary_hold_and_alerts_the_host(
    room_property, test_user, test_call_session, db_session
):
    db_session.add(_booking(room_property, "rooms", 2))
    await db_session.commit()

    result = await tool_handlers.handle_check_calendar(
        db_session, _calendar_args(room_property), test_user.id, test_call_session.id
    )
    assert "TEMPORARY HOLD" in result
    assert "AVAILABLE" not in result and "NOT available" not in result
    assert "2 rooms" in result

    escalations = (
        await db_session.scalars(select(Notification).where(Notification.channel == "escalation"))
    ).all()
    assert len(escalations) == 1
    assert "Double occupancy" in escalations[0].message and escalations[0].urgency == "high"
    ticket = await db_session.scalar(select(Ticket).where(Ticket.call_session_id == test_call_session.id))
    assert ticket is not None and ticket.summary.startswith("Double occupancy")

    # Asking again on the same call must not page the host a second time.
    await tool_handlers.handle_check_calendar(db_session, _calendar_args(room_property), test_user.id, test_call_session.id)
    assert len((await db_session.scalars(select(Notification).where(Notification.channel == "escalation"))).all()) == 1


async def test_hold_message_offers_the_free_rooms_when_some_remain(room_property, test_user, test_call_session, db_session):
    db_session.add(_booking(room_property, "rooms", 1))
    await db_session.commit()
    result = await tool_handlers.handle_check_calendar(
        db_session, _calendar_args(room_property), test_user.id, test_call_session.id
    )
    assert "Individual rooms are still open" in result


async def test_entire_place_booked_outright_is_a_plain_not_available_not_a_hold(room_property, test_user, db_session):
    db_session.add(_booking(room_property, "entire_place"))
    await db_session.commit()
    result = await tool_handlers.handle_check_calendar(db_session, _calendar_args(room_property), test_user.id)
    assert "NOT available" in result and "TEMPORARY HOLD" not in result
    assert (await db_session.scalars(select(Notification).where(Notification.channel == "escalation"))).all() == []


async def test_property_without_room_rental_is_unchanged(test_property, test_user, db_session):
    db_session.add(_booking(test_property, "entire_place"))
    await db_session.commit()
    result = await tool_handlers.handle_check_calendar(db_session, _calendar_args(test_property), test_user.id)
    assert "NOT available" in result and "TEMPORARY HOLD" not in result


# -- get_pricing ----------------------------------------------------------------------


def _pricing_args(property_, **kw):
    return GetPricingArgs(property_id=str(property_.id), check_in=IN, check_out=OUT, num_guests=kw.pop("num_guests", 2), **kw)


async def test_room_stay_is_quoted_at_the_room_rate_times_rooms_and_nights(room_property, db_session):
    result = await tool_handlers.handle_get_pricing(
        db_session, _pricing_args(room_property, stay_scope="rooms", rooms=2, num_guests=4)
    )
    assert "₹21,000 total for 3 night(s)" in result  # 3,500 x 2 rooms x 3 nights
    assert "2 rooms" in result


async def test_entire_place_quote_is_unchanged_by_room_rental(room_property, db_session):
    result = await tool_handlers.handle_get_pricing(db_session, _pricing_args(room_property, num_guests=8))
    assert "₹42,000 total for 3 night(s)" in result  # 14,000 x 3
    assert "room" not in result.lower()


async def test_pricing_rooms_asked_of_a_whole_place_only_property(test_property, db_session):
    result = await tool_handlers.handle_get_pricing(db_session, _pricing_args(test_property, stay_scope="rooms"))
    assert "only rented as the entire place" in result


# -- recommendation -------------------------------------------------------------------


def test_recommendation_note_says_both_ways_and_picks_for_the_guest(room_property):
    party = stay_options_note(room_property, 6, "bachelor party")
    stay = stay_options_note(room_property, 2, "night stay")
    unknown = stay_options_note(room_property, None, None)
    assert "ENTIRE place" in party and "Best fit for this guest: the ENTIRE place at ₹14,000" in party
    assert "Best fit for this guest: 1 room at ₹3,500" in stay
    assert "₹3,500 a night per room" in unknown and "Ask whether" in unknown


def test_no_note_for_a_whole_place_only_property(test_property):
    assert stay_options_note(test_property, 2, "party") == ""
    assert build_property_card(test_property).stay_options_note == ""


# -- the agent's prompt and knowledge base -------------------------------------------


def test_prompt_explains_stay_options_only_for_a_room_property(room_property, test_property, test_user):
    with_rooms = build_system_prompt(room_property, None, test_user)
    assert "rented TWO ways" in with_rooms and "TEMPORARY HOLD".lower() in with_rooms.lower()


def test_prompt_is_unchanged_for_a_whole_place_only_property(test_property, test_user):
    assert "Stay options" not in build_system_prompt(test_property, None, test_user)


def test_knowledge_base_records_that_rooms_can_be_rented(room_property):
    texts = [d.text for d in build_fact_chunks(room_property, [])]
    assert any("or by the room" in t and "4 rooms" in t for t in texts)
    assert any(t.startswith("Room: King bed") for t in texts)


def test_imported_one_way_booking_scope_faq_yields_to_the_room_rental_fact(room_property):
    entry = SimpleNamespace(
        id="e1",
        question="Is this the entire place or just a room?",
        answer="This listing is for the entire place: guests have the whole property to themselves.",
        category="booking_scope",
    )
    rented_both_ways = [d.text for d in build_fact_chunks(room_property, [entry])]
    assert not any("whole property to themselves" in t for t in rented_both_ways)
    assert any("or by the room" in t for t in rented_both_ways)
    # Switched back to the entire place only, the imported statement is true again and is kept.
    room_property.room_rental_enabled = False
    whole_only = [d.text for d in build_fact_chunks(room_property, [entry])]
    assert any("whole property to themselves" in t for t in whole_only)


# -- dashboard API --------------------------------------------------------------------------


async def test_cannot_turn_on_room_rental_without_a_price_room_count_and_capacity(client, test_property, test_user):
    response = await client.patch(
        f"/api/v1/properties/{test_property.id}", json={"room_rental_enabled": True}, headers=auth_headers_for(test_user)
    )
    assert response.status_code == 422


async def test_room_rental_settings_round_trip(client, test_property, test_user):
    response = await client.patch(
        f"/api/v1/properties/{test_property.id}",
        json={"room_rental_enabled": True, "room_base_price": 3000, "rentable_room_count": 3, "room_max_guests": 2},
        headers=auth_headers_for(test_user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["room_rental_enabled"] is True and body["rentable_room_count"] == 3 and body["room_base_price"] == 3000


async def test_host_can_enter_a_room_booking_and_it_shows_its_scope(client, room_property, test_user):
    response = await client.post(
        "/api/v1/bookings",
        json={
            "property_id": str(room_property.id),
            "check_in": IN.isoformat(),
            "check_out": OUT.isoformat(),
            "stay_scope": "rooms",
            "rooms_booked": 2,
        },
        headers=auth_headers_for(test_user),
    )
    assert response.status_code == 201
    assert (response.json()["stay_scope"], response.json()["rooms_booked"]) == ("rooms", 2)


async def test_room_booking_refused_for_a_property_not_rented_by_the_room(client, test_property, test_user):
    response = await client.post(
        "/api/v1/bookings",
        json={"property_id": str(test_property.id), "check_in": IN.isoformat(), "check_out": OUT.isoformat(), "stay_scope": "rooms"},
        headers=auth_headers_for(test_user),
    )
    assert response.status_code == 422


async def test_existing_booking_payload_still_means_the_entire_place(client, test_property, test_user):
    response = await client.post(
        "/api/v1/bookings",
        json={"property_id": str(test_property.id), "check_in": IN.isoformat(), "check_out": OUT.isoformat()},
        headers=auth_headers_for(test_user),
    )
    assert response.status_code == 201
    assert (response.json()["stay_scope"], response.json()["rooms_booked"]) == ("entire_place", None)


async def test_room_count_of_a_synced_booking_is_correctable_but_its_scope_is_not(client, room_property, test_user, db_session):
    booking = _booking(room_property, "rooms", 1, source_uid="airbnb-room-7")
    db_session.add(booking)
    await db_session.commit()
    url = f"/api/v1/bookings/{booking.id}/scope"
    ok = await client.patch(url, json={"stay_scope": "rooms", "rooms_booked": 3}, headers=auth_headers_for(test_user))
    assert ok.status_code == 200 and ok.json()["booking"]["rooms_booked"] == 3
    moved = await client.patch(url, json={"stay_scope": "entire_place"}, headers=auth_headers_for(test_user))
    assert moved.status_code == 422


# -- the real voice-tool wrappers (what the model actually calls) ------------------------


class _Params:
    def __init__(self):
        self.envelope = None
        self.result = None

    async def result_callback(self, result, properties=None):
        self.envelope = result
        self.result = result.get("result", result.get("message")) if isinstance(result, dict) else result


def _tool(tools, name):
    return next(t for t in tools if t.__name__ == name)


async def test_check_calendar_tool_accepts_rooms(room_property, test_user):
    from app.voice.tools import build_voice_tools

    tool = _tool(build_voice_tools(None, room_property.id, test_user.id), "check_calendar")
    params = _Params()
    await tool(
        params,
        property_id=str(room_property.id),
        check_in=IN.isoformat(),
        check_out=OUT.isoformat(),
        num_guests=3,
        stay_scope="rooms",
        rooms=2,
    )
    assert "AVAILABLE" in params.result and "2 rooms" in params.result


async def test_check_calendar_tool_rejects_an_unknown_stay_scope_without_crashing(room_property, test_user):
    from app.voice.tools import build_voice_tools

    tool = _tool(build_voice_tools(None, room_property.id, test_user.id), "check_calendar")
    params = _Params()
    await tool(params, property_id=str(room_property.id), check_in=IN.isoformat(), check_out=OUT.isoformat(), stay_scope="suite")
    assert params.envelope["success"] is False and params.envelope["error_type"] == "invalid_tool_arguments"
    assert "stay_scope" in params.result


async def test_get_pricing_tool_records_what_the_quote_was_for(room_property, test_user):
    from app.voice.conversation_state import ConversationState
    from app.voice.tools import build_voice_tools

    state = ConversationState()
    tool = _tool(build_voice_tools(None, room_property.id, test_user.id, state), "get_pricing")
    params = _Params()
    await tool(
        params,
        property_id=str(room_property.id),
        num_guests=4,
        check_in=IN.isoformat(),
        check_out=OUT.isoformat(),
        stay_scope="rooms",
        rooms=2,
    )
    assert "₹21,000" in params.result
    assert state.quoted_price["stay_scope"] == "rooms" and state.quoted_price["rooms"] == 2
    assert state.quoted_price["total"] == 21000


async def test_entire_place_quote_records_no_room_scope(room_property, test_user):
    from app.voice.conversation_state import ConversationState
    from app.voice.tools import build_voice_tools

    state = ConversationState()
    tool = _tool(build_voice_tools(None, room_property.id, test_user.id, state), "get_pricing")
    await tool(_Params(), property_id=str(room_property.id), num_guests=8, check_in=IN.isoformat(), check_out=OUT.isoformat())
    assert state.quoted_price["stay_scope"] is None and state.quoted_price["total"] == 42000


async def test_negotiating_a_room_stay_works_from_the_room_price(room_property, test_user):
    from app.voice.conversation_state import ConversationState
    from app.voice.tools import build_voice_tools

    state = ConversationState()
    tool = _tool(build_voice_tools(None, room_property.id, test_user.id, state), "negotiate_rate")
    params = _Params()
    await tool(
        params,
        property_id=str(room_property.id),
        check_in=IN.isoformat(),
        check_out=OUT.isoformat(),
        num_guests=4,
        stay_scope="rooms",
        rooms=2,
    )
    assert "2 rooms" in params.result
    assert "42,000" not in params.result  # never the entire-place rate
    assert state.quoted_price["stay_scope"] == "rooms" and state.quoted_price["total"] <= 21000


async def test_payment_link_is_not_offered_for_a_room_quote(room_property, test_user, db_session, monkeypatch):
    from app.services import billing_service

    monkeypatch.setattr(billing_service, "is_connected", lambda host: True)
    quote = {
        "property_name": room_property.name,
        "check_in": IN.isoformat(),
        "check_out": OUT.isoformat(),
        "total": 21000,
        "is_estimate": False,
        "stay_scope": "rooms",
        "rooms": 2,
    }
    result = await tool_handlers.handle_send_payment_link(
        db_session,
        host_user_id=test_user.id,
        property_id=str(room_property.id),
        quote=quote,
        guest_name="Asha",
        phone="9876543210",
        call_session_id=None,
    )
    assert "isn't available for room bookings" in result


async def test_hold_through_the_tool_leaves_the_host_one_double_occupancy_alert(
    room_property, test_user, test_call_session, db_session
):
    from app.voice.tools import build_voice_tools

    db_session.add(_booking(room_property, "rooms", 1))
    await db_session.commit()
    tool = _tool(build_voice_tools(test_call_session.id, room_property.id, test_user.id), "check_calendar")
    params = _Params()
    await tool(params, property_id=str(room_property.id), check_in=IN.isoformat(), check_out=OUT.isoformat(), num_guests=8)
    assert "TEMPORARY HOLD" in params.result
    alerts = (await db_session.scalars(select(Notification).where(Notification.channel == "escalation"))).all()
    assert len(alerts) == 1 and "Double occupancy" in alerts[0].message


# -- the Airbnb import (room / entire place recorded in the imported content) -------------


def test_import_records_that_a_room_listing_is_a_room():
    from app.services.airbnb_import import _extract_booking_scope_faq

    faq = _extract_booking_scope_faq({"category": "Room", "details": ["1 king bed", "Private attached bathroom"]})
    assert faq["category"] == "booking_scope"
    assert "room only, not the entire place" in faq["answer"] and "1 king bed" in faq["answer"]


def test_import_records_that_an_entire_place_listing_is_the_entire_place():
    from app.services.airbnb_import import _extract_booking_scope_faq

    faq = _extract_booking_scope_faq({"category": "Entire Place", "details": ["4 bedrooms"]})
    assert "entire place" in faq["answer"] and "room only" not in faq["answer"]


def test_import_adds_nothing_for_an_unknown_category():
    from app.services.airbnb_import import _extract_booking_scope_faq

    assert _extract_booking_scope_faq({"category": "Hotel room"}) is None
    assert _extract_booking_scope_faq({}) is None


def test_import_survives_location_details_given_as_a_list():
    # Regression: this shape (no HTML version) crashed the whole listing's import.
    from app.services.airbnb_import import _location_details_text

    assert _location_details_text({"location_details": [{"title": "Bengaluru, Karnataka, India", "value": None}]}) == (
        "Bengaluru, Karnataka, India"
    )
    assert _location_details_text({"location_details": [{"title": "Nearby", "value": "Metro 5 min"}]}) == "Nearby: Metro 5 min"
    assert _location_details_text({"location_details": "Quiet street", "location_details_html": None}) == "Quiet street"
    assert _location_details_text({}) == ""
