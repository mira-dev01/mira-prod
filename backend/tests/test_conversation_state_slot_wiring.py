"""Covers Phase 1.2 (slot capture wired into tool wrappers) and Phase 1.4
(recommend_properties backfills num_guests/budget from state.slots) of
documentation/agent-conversation-improvement.md -- separate from
test_conversation_state.py (dataclass-level tests) and test_property_lock.py
(the original property_id-lock tests), since these specifically exercise the
tool-wrapper -> ConversationState wiring end to end.
"""

import uuid
from datetime import date, timedelta

from app.models.property import Property
from app.voice.conversation_state import ConversationState
from app.voice.tools import build_voice_tools


class _FakeFunctionCallParams:
    def __init__(self):
        self.result = None

    async def result_callback(self, result, **kwargs):
        self.result = result


async def _small_property(db_session, test_user, name, max_guests):
    property_ = Property(
        user_id=test_user.id,
        name=name,
        city="Goa",
        exophone=f"+9180{uuid.uuid4().int % 10**8:08d}",
        base_price=3500,
        max_guests=max_guests,
    )
    db_session.add(property_)
    await db_session.commit()
    await db_session.refresh(property_)
    return property_


async def test_update_lead_writes_slots_without_clobbering_earlier_fields(test_property, db_session, test_user):
    """Reproduces the exact bug class Phase 1.2's own verify step calls out:
    a tool call that only supplies `phone` must not erase a `num_guests` a
    much earlier call already set."""
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    update_lead = next(t for t in tools if t.__name__ == "update_lead")

    params1 = _FakeFunctionCallParams()
    await update_lead(params1, num_guests=6, budget=15000, preferred_location="Goa")
    assert state.slots["num_guests"] == 6
    assert state.slots["budget"] == 15000
    assert state.slots["preferred_location"] == "Goa"

    # A later turn only reports a phone number -- must not touch the fields
    # already known.
    params2 = _FakeFunctionCallParams()
    await update_lead(params2, phone="9123456789")
    assert state.slots["num_guests"] == 6
    assert state.slots["budget"] == 15000
    assert state.slots["preferred_location"] == "Goa"
    assert state.slots["phone"] == "9123456789"


async def test_update_lead_writes_dates_as_iso_strings(test_property, db_session, test_user):
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    update_lead = next(t for t in tools if t.__name__ == "update_lead")

    today = date.today()
    check_in = (today + timedelta(days=10)).isoformat()
    check_out = (today + timedelta(days=12)).isoformat()
    params = _FakeFunctionCallParams()
    await update_lead(params, check_in=check_in, check_out=check_out)
    assert state.slots["check_in"] == check_in
    assert state.slots["check_out"] == check_out


async def test_check_calendar_sets_slots_and_checking_availability_goal(test_property, db_session, test_user):
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    check_calendar = next(t for t in tools if t.__name__ == "check_calendar")

    today = date.today()
    params = _FakeFunctionCallParams()
    await check_calendar(
        params,
        property_id=str(test_property.id),
        check_in=(today + timedelta(days=5)).isoformat(),
        check_out=(today + timedelta(days=7)).isoformat(),
        num_guests=3,
    )
    assert state.slots["num_guests"] == 3
    assert state.conversation_goal == "checking_availability"


async def test_get_pricing_records_quoted_price_in_state(test_property, db_session, test_user):
    """Phase 4.1 (documentation/agent-conversation-improvement.md): the real
    quoted total is recorded into state so the prompt can say "you already
    quoted ₹X for these dates" instead of relying on the model to recall it
    from a long transcript."""
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    get_pricing = next(t for t in tools if t.__name__ == "get_pricing")

    today = date.today()
    check_in = (today + timedelta(days=5)).isoformat()
    check_out = (today + timedelta(days=7)).isoformat()
    params = _FakeFunctionCallParams()
    await get_pricing(
        params,
        property_id=str(test_property.id),
        check_in=check_in,
        check_out=check_out,
        num_guests=2,
    )

    assert state.quoted_price is not None
    assert state.quoted_price["check_in"] == check_in
    assert state.quoted_price["check_out"] == check_out
    assert state.quoted_price["total"] > 0
    assert state.quoted_price["property_name"] == test_property.name


async def test_get_pricing_later_quote_overwrites_earlier_one(test_property, db_session, test_user):
    """A later quote (different dates, or the same dates re-quoted with a
    discount applied) is always the current one the model should
    reference -- confirmed this overwrites rather than merges/stacks."""
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    get_pricing = next(t for t in tools if t.__name__ == "get_pricing")

    today = date.today()
    await get_pricing(
        _FakeFunctionCallParams(),
        property_id=str(test_property.id),
        check_in=(today + timedelta(days=5)).isoformat(),
        check_out=(today + timedelta(days=7)).isoformat(),
        num_guests=2,
    )
    first_quote = state.quoted_price

    new_check_in = (today + timedelta(days=20)).isoformat()
    new_check_out = (today + timedelta(days=22)).isoformat()
    await get_pricing(
        _FakeFunctionCallParams(),
        property_id=str(test_property.id),
        check_in=new_check_in,
        check_out=new_check_out,
        num_guests=2,
    )

    assert state.quoted_price != first_quote
    assert state.quoted_price["check_in"] == new_check_in


async def test_escalate_to_host_marks_escalated_and_freezes_goal(test_property, db_session, test_user):
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    escalate_to_host = next(t for t in tools if t.__name__ == "escalate_to_host")
    update_lead = next(t for t in tools if t.__name__ == "update_lead")

    params = _FakeFunctionCallParams()
    await escalate_to_host(params, property_id=str(test_property.id), reason="guest needs a human", urgency="medium")
    assert state.escalated is True
    assert state.conversation_goal == "escalating"

    # A later update_lead call must not silently move the goal away from
    # "escalating" -- the call already needed a human.
    later_params = _FakeFunctionCallParams()
    await update_lead(later_params, num_guests=2)
    assert state.conversation_goal == "escalating"


async def test_recommend_properties_backfills_num_guests_from_state_slots(db_session, test_user):
    """Reproduces catalogue item C1 (documentation/agent-conversation-improvement.md
    Phase 0.2) directly: a guest states their count via update_lead in one
    turn, then a later recommend_properties call omits num_guests entirely.
    Without the Phase 1.4 backfill, apply_guest_count_filter
    (filter_builder.py) would apply ZERO capacity filtering, returning
    2-guest properties to a stated 4-person group -- confirmed against the
    real C1 transcript. With the backfill, the search must still filter to
    max_guests >= 4."""
    small = await _small_property(db_session, test_user, "Small Studio", max_guests=2)
    big_enough = await _small_property(db_session, test_user, "Family Suite", max_guests=4)

    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    update_lead = next(t for t in tools if t.__name__ == "update_lead")
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    # Turn 2: guest says "we're 4 people" via update_lead.
    lead_params = _FakeFunctionCallParams()
    await update_lead(lead_params, num_guests=4)
    assert state.slots["num_guests"] == 4

    # Turn 5: the LLM calls recommend_properties WITHOUT num_guests -- the
    # exact real-call failure mode.
    rec_params = _FakeFunctionCallParams()
    await recommend_properties(rec_params, preferred_location="Goa")

    assert "Family Suite" in rec_params.result
    assert "Small Studio" not in rec_params.result


async def test_recommend_properties_explicit_arg_still_wins_over_state(db_session, test_user):
    """The backfill only fills a GAP -- if the model does supply num_guests
    explicitly, that value is used even if it differs from state.slots
    (e.g. a genuine correction: 'actually just 2 of us for this trip')."""
    small = await _small_property(db_session, test_user, "Small Studio", max_guests=2)
    big = await _small_property(db_session, test_user, "Family Suite", max_guests=6)

    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    update_lead = next(t for t in tools if t.__name__ == "update_lead")
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    await update_lead(_FakeFunctionCallParams(), num_guests=6)

    params = _FakeFunctionCallParams()
    await recommend_properties(params, num_guests=2, preferred_location="Goa")

    assert "Small Studio" in params.result


async def test_recommend_properties_records_shown_options_and_sets_awaiting_selection(db_session, test_user):
    property_ = await _small_property(db_session, test_user, "Ocean View", max_guests=4)

    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    await recommend_properties(_FakeFunctionCallParams(), preferred_location="Goa")

    assert len(state.recommendations_shown) >= 1
    assert state.conversation_goal == "awaiting_selection"


async def test_lock_property_after_recommendation_sets_guest_accepted_id(db_session, test_user):
    property_ = await _small_property(db_session, test_user, "Ocean View", max_guests=4)

    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")
    get_pricing = next(t for t in tools if t.__name__ == "get_pricing")

    await recommend_properties(_FakeFunctionCallParams(), preferred_location="Goa")
    assert str(property_.id) in [o["property_id"] for o in state.recommendations_shown]

    today = date.today()
    await get_pricing(
        _FakeFunctionCallParams(),
        property_id=str(property_.id),
        check_in=(today + timedelta(days=1)).isoformat(),
        check_out=(today + timedelta(days=3)).isoformat(),
        num_guests=4,
    )
    assert state.guest_accepted_property_id == str(property_.id)


async def test_recommend_properties_wrapper_excludes_booked_property_using_state_dates(db_session, test_user):
    """Phase 2.4 (documentation/agent-conversation-improvement.md), wired
    end-to-end through the actual tool wrapper (not just orchestrator.py
    directly, per test_property_retrieval_orchestrator.py's own coverage of
    that layer): a guest gives dates via update_lead, then a later
    recommend_properties call (which never mentions dates at all -- they
    aren't even an argument on this tool) must still exclude a property
    already booked for those exact dates."""
    from app.models.booking import Booking

    booked = await _small_property(db_session, test_user, "Booked Villa", max_guests=4)
    open_villa = await _small_property(db_session, test_user, "Open Villa", max_guests=4)

    check_in = date.today() + timedelta(days=10)
    check_out = check_in + timedelta(days=2)
    db_session.add(Booking(property_id=booked.id, check_in=check_in, check_out=check_out, status="confirmed"))
    await db_session.commit()

    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    update_lead = next(t for t in tools if t.__name__ == "update_lead")
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    await update_lead(_FakeFunctionCallParams(), check_in=check_in.isoformat(), check_out=check_out.isoformat())
    assert state.slots["check_in"] == check_in.isoformat()

    params = _FakeFunctionCallParams()
    await recommend_properties(params, preferred_location="Goa")

    assert "Open Villa" in params.result
    assert "Booked Villa" not in params.result
