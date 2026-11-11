"""Alternative dates must be real: the nearest verified-free stay on each side of
the guest's dates, never a guess (calendar_service.closest_open_windows), told to
the agent by check_calendar and enforced on the spoken reply by the guard."""

import random
from datetime import date, timedelta

import pytest
from pipecat.frames.frames import (
    FunctionCallFromLLM,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.tests.utils import SleepFrame, run_test
from sqlalchemy import delete

from app.models.booking import Booking
from app.schemas.tool import CheckCalendarArgs
from app.services import calendar_service, tool_handlers
from app.services.calendar_service import OpenWindow, closest_open_windows, is_available
from app.utils.dates import today_ist
from app.utils.spoken_dates import dates_in_ranges, extract_stated_dates, unverified_dates
from app.voice.property_recommendation_guard import PropertyRecommendationGuardProcessor

TODAY = today_ist()


def _d(offset: int) -> date:
    return TODAY + timedelta(days=offset)


async def _block(db, property_, start: int, end: int, *, status="confirmed", kind="reservation"):
    db.add(
        Booking(
            property_id=property_.id,
            check_in=_d(start),
            check_out=_d(end),
            platform="manual",
            status=status,
            kind=kind,
        )
    )
    await db.commit()


def _ranges(windows):
    return [(w.check_in, w.check_out) for w in windows]


# --- the search itself ---------------------------------------------------------


async def test_nearest_window_on_each_side_nearest_first(test_property, db_session):
    # Guest wants 21..23 (2 nights); the property is booked 18..24.
    await _block(db_session, test_property, 18, 24)

    windows = await closest_open_windows(db_session, test_property, "entire_place", _d(21), 2)

    # Later: 24..26 (3 days later). Earlier: 16..18 (5 days earlier, ends the day the booking starts).
    assert _ranges(windows) == [(_d(24), _d(26)), (_d(16), _d(18))]
    assert [w.shift_days for w in windows] == [3, -5]


async def test_a_tie_in_distance_lists_the_later_window_first(test_property, db_session):
    await _block(db_session, test_property, 18, 24)

    # Booked 18..24 and the guest asks 20..22: the free stays are 16..18 (4 days earlier)
    # and 24..26 (4 days later) -- equally near, so the later one is listed first.
    windows = await closest_open_windows(db_session, test_property, "entire_place", _d(20), 2)

    assert [w.shift_days for w in windows] == [4, -4]
    for w in windows:
        assert await is_available(db_session, test_property.id, w.check_in, w.check_out)


async def test_back_to_back_stays_are_allowed(test_property, db_session):
    # A stay may start on the day another ends and end on the day another starts.
    await _block(db_session, test_property, 10, 14)
    await _block(db_session, test_property, 16, 20)

    windows = await closest_open_windows(db_session, test_property, "entire_place", _d(11), 2)

    assert (_d(14), _d(16)) in _ranges(windows)


async def test_never_offers_a_start_before_today(test_property, db_session):
    await _block(db_session, test_property, 1, 6)

    windows = await closest_open_windows(db_session, test_property, "entire_place", _d(2), 2)

    assert windows
    assert all(w.check_in >= TODAY for w in windows)
    # Every earlier stay would start before today or overlap the booking, so only later ones remain.
    assert all(w.shift_days > 0 for w in windows)


async def test_nothing_within_the_horizon_returns_empty(test_property, db_session):
    await _block(db_session, test_property, -10, 200)

    windows = await closest_open_windows(db_session, test_property, "entire_place", _d(30), 2)

    assert windows == []


async def test_a_single_side_is_returned_when_only_one_has_room(test_property, db_session):
    await _block(db_session, test_property, 5, 200)

    windows = await closest_open_windows(db_session, test_property, "entire_place", _d(20), 2)

    assert [w.shift_days < 0 for w in windows] == [True]
    assert windows[0].check_out <= _d(5)


async def test_payment_holds_block_like_bookings(test_property, db_session):
    await _block(db_session, test_property, 20, 23, kind="hold")

    windows = await closest_open_windows(db_session, test_property, "entire_place", _d(21), 2)

    for w in windows:
        assert w.check_out <= _d(20) or w.check_in >= _d(23)


async def test_cancelled_bookings_do_not_block(test_property, db_session):
    await _block(db_session, test_property, 20, 23, status="cancelled")

    windows = await closest_open_windows(db_session, test_property, "entire_place", _d(21), 2)

    assert [w.shift_days for w in windows] == [1, -1]  # right next to the request, both sides


async def test_the_accept_rule_can_exclude_a_window(test_property, db_session):
    await _block(db_session, test_property, 20, 23)
    seen = []

    async def accept(start, end):
        seen.append(start)
        return start != _d(23)  # reject the nearest later window

    windows = await closest_open_windows(db_session, test_property, "entire_place", _d(21), 2, accept=accept)

    assert _d(23) in seen
    assert all(w.check_in != _d(23) for w in windows)
    assert any(w.shift_days > 0 for w in windows)  # it moved on to the next later one


async def test_room_scope_is_blocked_by_an_entire_place_booking(test_property, db_session):
    test_property.room_rental_enabled = True
    test_property.rentable_room_count = 3
    test_property.room_base_price = 1000
    test_property.room_max_guests = 2
    await db_session.commit()
    await _block(db_session, test_property, 20, 23)

    windows = await closest_open_windows(db_session, test_property, "rooms", _d(21), 2, rooms=1)

    for w in windows:
        assert w.check_out <= _d(20) or w.check_in >= _d(23)


async def test_every_window_matches_a_brute_force_oracle(test_property, db_session):
    """Random calendars: each suggested window must be free by the authoritative
    check, and be the NEAREST free one on its side (checked day by day)."""
    rng = random.Random(20261110)
    for _ in range(25):
        await db_session.execute(delete(Booking).where(Booking.property_id == test_property.id))
        await db_session.commit()
        for _b in range(rng.randint(0, 6)):
            start = rng.randint(-5, 50)
            await _block(db_session, test_property, start, start + rng.randint(1, 8))
        nights = rng.randint(1, 4)
        wanted = _d(rng.randint(0, 40))

        windows = await closest_open_windows(db_session, test_property, "entire_place", wanted, nights)

        for sign in (1, -1):
            expected = None
            for offset in range(1, calendar_service.ALTERNATIVE_HORIZON_DAYS + 1):
                start = wanted + timedelta(days=sign * offset)
                if start < TODAY:
                    continue
                if await is_available(db_session, test_property.id, start, start + timedelta(days=nights)):
                    expected = start
                    break
            got = [w for w in windows if (w.shift_days > 0) == (sign > 0)]
            if expected is None:
                assert got == []
            else:
                assert [w.check_in for w in got] == [expected]
                assert (got[0].check_out - got[0].check_in).days == nights


# --- check_calendar's answer -----------------------------------------------------


async def test_check_calendar_lists_only_verified_alternatives(test_property, db_session):
    await _block(db_session, test_property, 20, 24)
    args = CheckCalendarArgs(property_id=str(test_property.id), check_in=_d(21), check_out=_d(23))
    heard = []

    result = await tool_handlers.handle_check_calendar(
        db_session, args, on_alternatives=lambda p, ci, co, w: heard.append((ci, co, list(w)))
    )

    assert "NOT available" in result
    assert "Offer ONLY these" in result
    assert heard and heard[0][0] == _d(21) and heard[0][1] == _d(23)
    for w in heard[0][2]:
        assert w.check_in.isoformat() in result and w.check_out.isoformat() in result


async def test_check_calendar_says_so_when_there_are_no_alternatives(test_property, db_session):
    await _block(db_session, test_property, -10, 200)
    args = CheckCalendarArgs(property_id=str(test_property.id), check_in=_d(21), check_out=_d(23))
    heard = []

    result = await tool_handlers.handle_check_calendar(db_session, args, on_alternatives=lambda *a: heard.append(a))

    assert "Do NOT suggest any dates" in result
    assert heard[0][3] == []


async def test_check_calendar_does_not_offer_alternatives_when_the_dates_are_free(test_property, db_session):
    args = CheckCalendarArgs(property_id=str(test_property.id), check_in=_d(21), check_out=_d(23))
    heard = []

    result = await tool_handlers.handle_check_calendar(db_session, args, on_alternatives=lambda *a: heard.append(a))

    assert "AVAILABLE" in result and "NOT" not in result
    assert heard == []


async def test_a_failed_search_leaves_no_dates_to_offer(test_property, db_session, monkeypatch):
    await _block(db_session, test_property, 20, 24)

    async def boom(*_a, **_k):
        raise RuntimeError("calendar search down")

    monkeypatch.setattr(calendar_service, "closest_open_windows", boom)
    args = CheckCalendarArgs(property_id=str(test_property.id), check_in=_d(21), check_out=_d(23))

    result = await tool_handlers.handle_check_calendar(db_session, args)

    assert "NO dates to offer" in result


async def test_a_saturday_only_window_is_not_offered_when_the_saturday_rule_is_on(test_property, db_session):
    test_property.saturday_minimum_stay_enabled = True
    await db_session.commit()
    saturday = next(_d(i) for i in range(20, 40) if _d(i).weekday() == 5)
    offset = (saturday - TODAY).days
    # Book the guest's own night so the nearest 1-night alternatives include Saturday-start ones.
    await _block(db_session, test_property, offset - 1, offset)
    args = CheckCalendarArgs(property_id=str(test_property.id), check_in=_d(offset - 1), check_out=_d(offset))
    heard = []

    await tool_handlers.handle_check_calendar(db_session, args, on_alternatives=lambda p, ci, co, w: heard.append(w))

    for w in heard[0]:
        nights = [w.check_in + timedelta(days=i) for i in range((w.check_out - w.check_in).days)]
        assert not any(n.weekday() == 5 for n in nights)


# --- the guard -----------------------------------------------------------------


def _calendar_call() -> FunctionCallsStartedFrame:
    return FunctionCallsStartedFrame(
        function_calls=[FunctionCallFromLLM(function_name="check_calendar", tool_call_id="tc", arguments={}, context=None)]
    )


async def _spoken(reply: str, *, windows=None) -> str:
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("check_calendar", {"property_name": "Casa Blu", "available": False})
    guard.record_tool_result(
        "check_calendar_alternatives",
        {
            "property_name": "Casa Blu",
            "requested": (date(2026, 12, 5), date(2026, 12, 7)),
            "windows": windows if windows is not None else [(date(2026, 12, 7), date(2026, 12, 9))],
        },
    )
    down, _ = await run_test(
        guard,
        frames_to_send=[
            _calendar_call(),
            SleepFrame(sleep=0.02),
            LLMFullResponseStartFrame(),
            LLMTextFrame(reply),
            LLMFullResponseEndFrame(),
        ],
    )
    return "".join(f.text for f in down if isinstance(f, LLMTextFrame))


@pytest.mark.asyncio
async def test_guard_lets_a_reply_naming_only_verified_dates_through():
    reply = "Casa Blu isn't free then, but 7 December to 9 December is open. Shall I check that for you?"
    assert await _spoken(reply) == reply


@pytest.mark.asyncio
async def test_guard_replaces_a_reply_that_names_an_unchecked_date():
    spoken = await _spoken("Casa Blu isn't free then, but how about 11 December to 13 December?")

    assert "11 December" not in spoken
    assert "7 December to 9 December" in spoken


@pytest.mark.asyncio
async def test_guard_replaces_an_unchecked_bare_ordinal():
    spoken = await _spoken("That's booked. The 12th to the 14th should be free though.")

    assert "12th" not in spoken
    assert "7 December to 9 December" in spoken


@pytest.mark.asyncio
async def test_guard_never_invents_dates_when_none_were_found():
    spoken = await _spoken("Sorry, those dates are taken. What about 20 December to 22 December?", windows=[])

    assert "20 December" not in spoken
    assert "don't see an open stay" in spoken


@pytest.mark.asyncio
async def test_guard_leaves_a_reply_with_no_dates_alone():
    reply = "I'm sorry, Casa Blu isn't available then. Would you like me to look at other dates?"
    assert await _spoken(reply) == reply


@pytest.mark.asyncio
async def test_guard_allows_the_guests_own_requested_dates_to_be_repeated():
    reply = "Casa Blu is booked from 5 December to 7 December, but 7 December to 9 December is open."
    assert await _spoken(reply) == reply


# --- reading dates out of text ---------------------------------------------------


def test_extract_stated_dates_handles_the_common_spoken_shapes():
    found = lambda t: {(d.day, d.month) for d in extract_stated_dates(t)}  # noqa: E731
    assert found("6 to 8 December") == {(6, 12), (8, 12)}
    assert found("December 6th to 8th") == {(6, 12), (8, 12)}
    assert found("the 6th of October") == {(6, 10)}
    assert found("the 6th to the 8th") == {(6, None), (8, None)}
    assert found("2026-12-06") == {(6, 12)}
    assert found("8 adults for 2 nights, check-in from 1 PM") == set()


def test_unverified_dates_compares_day_and_month():
    allowed = dates_in_ranges([(date(2026, 12, 7), date(2026, 12, 9))])
    assert unverified_dates("7 December to 9 December", allowed) == set()
    assert {(d.day, d.month) for d in unverified_dates("7 November", allowed)} == {(7, 11)}
    assert unverified_dates("the 8th", allowed) == set()  # inside the verified stay
    assert {d.day for d in unverified_dates("the 20th", allowed)} == {20}


def test_open_window_is_a_plain_value():
    w = OpenWindow(date(2026, 12, 7), date(2026, 12, 9), 2)
    assert (w.check_out - w.check_in).days == 2


# --- the real tool wrapper feeding the real guard --------------------------------------


class _ToolParams:
    def __init__(self):
        self.envelope = None
        self.tool_call_id = "tc-1"
        self.context = None

    async def result_callback(self, result, properties=None):
        self.envelope = result


async def _call_check_calendar(test_user, test_property, check_in: date, check_out: date, guard):
    from app.voice.conversation_state import ConversationState
    from app.voice.tools import build_voice_tools

    state = ConversationState()
    state.set_slot("guest_name", "Asha")  # the collect gate wants name and phone before availability
    state.set_slot("phone", "9876543210")
    tools = build_voice_tools(
        call_session_id=None,
        property_id=None,
        host_user_id=test_user.id,
        conversation_state=state,
        property_recommendation_guard=guard,
    )
    tool = next(t for t in tools if t.__name__ == "check_calendar")
    params = _ToolParams()
    await tool(params, property_id=str(test_property.id), check_in=check_in.isoformat(), check_out=check_out.isoformat(), num_guests=2)
    return params.envelope


async def _guarded_reply(guard, reply: str) -> str:
    down, _ = await run_test(
        guard,
        frames_to_send=[
            _calendar_call(),
            SleepFrame(sleep=0.02),
            LLMFullResponseStartFrame(),
            LLMTextFrame(reply),
            LLMFullResponseEndFrame(),
        ],
    )
    return "".join(f.text for f in down if isinstance(f, LLMTextFrame))


async def test_the_real_tool_feeds_the_guard_so_an_invented_date_never_reaches_the_guest(test_user, test_property, db_session):
    await _block(db_session, test_property, 20, 24)
    guard = PropertyRecommendationGuardProcessor()

    envelope = await _call_check_calendar(test_user, test_property, _d(21), _d(23), guard)
    assert envelope["status"] == "ok" and "Offer ONLY these" in envelope["result"]

    # An alternative the tool listed: allowed through.
    later = _d(24)
    honest = f"That's booked, but {later.day} {later.strftime('%B')} to {(later + timedelta(days=2)).day} {(later + timedelta(days=2)).strftime('%B')} is open."
    guard2 = PropertyRecommendationGuardProcessor()
    await _call_check_calendar(test_user, test_property, _d(21), _d(23), guard2)
    assert await _guarded_reply(guard2, honest) == honest

    # A date the model made up (a free-looking day the tool never returned): replaced.
    invented = _d(40)
    spoken = await _guarded_reply(guard, f"How about {invented.day} {invented.strftime('%B')} to {(invented + timedelta(days=2)).day} {(invented + timedelta(days=2)).strftime('%B')}?")
    assert f"{invented.day} {invented.strftime('%B')}" not in spoken
    assert "closest open dates" in spoken


async def test_a_failed_calendar_check_tells_the_agent_to_treat_the_dates_as_unavailable(test_user, test_property, monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(tool_handlers, "handle_check_calendar", boom)

    envelope = await _call_check_calendar(test_user, test_property, _d(21), _d(23), PropertyRecommendationGuardProcessor())

    assert envelope["status"] == "internal_error"
    text = envelope["result"] if "result" in envelope else envelope.get("message", "")
    assert "NOT available" in text and "never suggest any other dates" in text
