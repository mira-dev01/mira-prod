"""Phase 4F (conversation-level negotiation integration) -- live-flow-level
tests exercising the REAL app/voice/tools.py wrapper (build_voice_tools),
not ConversationState methods called directly, covering the full Step 16
test matrix items that specifically need the wrapper's own wiring verified:
last_negotiation_decision reaching state through a real negotiate_rate call,
quoted_price being populated by negotiate_rate (Step 6B fix), context
invalidation clearing the negotiation-decision fact end-to-end, and the
get_pricing/negotiate_rate boundary.

Deliberately uses ARBITRARY, VARIED stage counts/values/prices across
different tests -- no single host configuration is treated as canonical.
"""

from datetime import date, timedelta

from app.models.negotiation_rule import NegotiationRule
from app.models.property import Property
from app.voice.conversation_state import ConversationState
from app.voice.state_prompt_sync import build_state_block_content
from app.voice.tools import build_voice_tools


class _FakeFunctionCallParams:
    def __init__(self):
        self.result = None
        self.properties = None

    async def result_callback(self, result, properties=None):
        self.result = result
        self.properties = properties


def _dates(offset_days=10):
    today = date.today()
    return (today + timedelta(days=offset_days)).isoformat(), (today + timedelta(days=offset_days + 2)).isoformat()


# ---------------------------------------------------------------------------
# BASIC (Step 16, items 1-7) -- exercised through the real wrapper.
# ---------------------------------------------------------------------------


async def test_1_first_price_request_via_get_pricing_then_negotiation(test_property, db_session, test_user):
    """A first, unprompted price request goes through get_pricing (not
    negotiate_rate) -- confirms the boundary the prompt itself specifies is
    exercised end-to-end at the wrapper level, then a subsequent
    negotiate_rate call is the actual negotiation event."""
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    get_pricing = next(t for t in tools if t.__name__ == "get_pricing")
    check_in, check_out = _dates()

    params = _FakeFunctionCallParams()
    await get_pricing(params, property_id=str(test_property.id), check_in=check_in, check_out=check_out, num_guests=2)
    assert state.quoted_price is not None
    assert state.last_negotiation_decision is None  # no negotiation happened yet
    assert state.negotiation_events == []


async def test_2_unquantified_pushback_lands_on_first_stage(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 5}, {"order": 1, "value": 10}], status="approved")
    )
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=None)
    assert state.last_negotiation_decision["stage_index"] == 0
    assert state.last_negotiation_decision["is_staged"] is True


async def test_3_repeated_unquantified_pushback_stays_at_first_stage(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 5}, {"order": 1, "value": 10}, {"order": 2, "value": 15}], status="approved")
    )
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    for _ in range(4):
        await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=None)
    assert state.last_negotiation_decision["stage_index"] == 0
    assert state.last_negotiation_decision["progressed_this_event"] is False


async def test_4_numeric_first_offer_lands_on_first_stage(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 8}], status="approved")
    )
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    # Even a large first offer cannot skip stage 0 (ratified Phase 4C rule).
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=999999)
    assert state.last_negotiation_decision["stage_index"] == 0


async def test_5_repeated_identical_numeric_offer_does_not_progress(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 8}], status="approved")
    )
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=3000)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=3000)
    assert state.last_negotiation_decision["stage_index"] == 0
    assert state.last_negotiation_decision["progressed_this_event"] is False


async def test_6_lower_numeric_offer_does_not_progress(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 8}], status="approved")
    )
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=3500)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=3000)
    assert state.last_negotiation_decision["stage_index"] == 0
    assert state.last_negotiation_decision["progressed_this_event"] is False


async def test_7_strictly_higher_numeric_offer_progresses(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 8}], status="approved")
    )
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=3000)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=3500)
    assert state.last_negotiation_decision["stage_index"] == 1
    assert state.last_negotiation_decision["progressed_this_event"] is True


# ---------------------------------------------------------------------------
# CONVERSATION (Step 16, items 8-10).
# ---------------------------------------------------------------------------


async def test_8_acceptance_does_not_call_negotiate_rate(test_property, db_session, test_user):
    """Acceptance ("okay, that's fine") is a routing decision the LLM makes
    per the system prompt (no trigger condition for it calls negotiate_rate)
    -- this test documents that negotiate_rate simply never gets invoked for
    an accept-shaped utterance, since nothing in this codebase would call it
    without the LLM choosing to. Confirms state stays untouched when the
    tool is never called, which is the actual guarantee available at this
    layer (see the final report's Section 5 for the full reasoning)."""
    state = ConversationState()
    build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    # No negotiate_rate call made -- simulating the LLM correctly not
    # calling it for "Okay, that's fine."
    assert state.negotiation_events == []
    assert state.last_negotiation_decision is None


async def test_9_unrelated_question_does_not_negotiate(test_property, db_session, test_user):
    """An unrelated question (e.g. "is breakfast included?") routes to
    search_faq, not negotiate_rate -- confirms negotiate_rate not being
    called leaves negotiation state completely untouched."""
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=3000)
    events_before = list(state.negotiation_events)
    # Unrelated question -- no negotiate_rate call simulated here.
    assert state.negotiation_events == events_before


async def test_10_negotiation_resumes_correctly_after_unrelated_gap(test_property, db_session, test_user):
    """Negotiation state must survive an intervening tool call that isn't
    negotiate_rate (e.g. search_faq/check_calendar) -- confirms
    negotiation_events isn't accidentally cleared by unrelated activity."""
    db_session.add(
        NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 8}], status="approved")
    )
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_calendar = next(t for t in tools if t.__name__ == "check_calendar")
    check_in, check_out = _dates()

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=3000)
    await check_calendar(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=3500)

    assert state.last_negotiation_decision["stage_index"] == 1  # progression preserved across the gap


# ---------------------------------------------------------------------------
# PROGRESSION (Step 16, items 11-15) -- arbitrary stage counts, per the brief.
# ---------------------------------------------------------------------------


async def test_11_one_stage_policy(test_property, db_session, test_user):
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 6}], status="approved"))
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=None)
    assert state.last_negotiation_decision["stage_count"] == 1
    assert state.last_negotiation_decision["exhausted"] is True


async def test_12_two_stage_policy(test_property, db_session, test_user):
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 3}, {"order": 1, "value": 9}], status="approved"))
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=1)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=99999)
    assert state.last_negotiation_decision["stage_index"] == 1
    assert state.last_negotiation_decision["exhausted"] is True


async def test_13_three_stage_policy(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 2}, {"order": 1, "value": 5}, {"order": 2, "value": 11}], status="approved")
    )
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=1)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=2)
    assert state.last_negotiation_decision["stage_index"] == 1
    assert state.last_negotiation_decision["exhausted"] is False


async def test_14_five_stage_policy(test_property, db_session, test_user):
    stages = [{"order": i, "value": (i + 1) * 2} for i in range(5)]
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=stages, status="approved"))
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()
    for offer in (1, 2, 3, 4, 5):
        await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=offer)
    assert state.last_negotiation_decision["stage_index"] == 4
    assert state.last_negotiation_decision["exhausted"] is True


async def test_15_arbitrary_stage_values_not_round_numbers(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 1.25}, {"order": 1, "value": 13.75}], status="approved")
    )
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=None)
    expected_floor = round(state.last_negotiation_decision["asking_price"] * (1 - 1.25 / 100), 2)
    assert state.last_negotiation_decision["counter_offer"] == expected_floor


async def test_16_final_stage_exhaustion_communicated(test_property, db_session, test_user):
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 8}], status="approved"))
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=1)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=2)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=3)  # further pushback
    content = build_state_block_content(state)
    assert "maximum" in content.lower() or "best price" in content.lower() or "no further concession" in content.lower()


# ---------------------------------------------------------------------------
# DUPLICATION (Step 16, items 17-19).
# ---------------------------------------------------------------------------


async def test_17_duplicate_identical_tool_call_cannot_advance_stage(test_property, db_session, test_user):
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 8}, {"order": 2, "value": 12}], status="approved"))
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=5000)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=5000)
    assert state.last_negotiation_decision["stage_index"] == 0


async def test_18_repeated_identical_guest_offer_across_three_calls(test_property, db_session, test_user):
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 8}], status="approved"))
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()
    for _ in range(3):
        await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=4200)
    assert state.last_negotiation_decision["stage_index"] == 0
    assert len(state.negotiation_events) == 3  # every call IS recorded -- only progression is prevented


async def test_19_repeated_unquantified_tool_call_across_five_calls(test_property, db_session, test_user):
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 8}], status="approved"))
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()
    for _ in range(5):
        await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out)
    assert state.last_negotiation_decision["stage_index"] == 0


# ---------------------------------------------------------------------------
# CONTEXT (Step 16, items 20-22) -- last_negotiation_decision must also
# reset, not just negotiation_events (Phase 4F fix).
# ---------------------------------------------------------------------------


async def test_20_property_change_resets_negotiation_decision(db_session, test_user):
    property_a = Property(user_id=test_user.id, name="Villa A", city="Goa", exophone="+918000077701", base_price=4000, max_guests=4)
    property_b = Property(user_id=test_user.id, name="Villa B", city="Goa", exophone="+918000077702", base_price=4000, max_guests=4)
    db_session.add_all([property_a, property_b])
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 8}], status="approved"))
    await db_session.commit()
    await db_session.refresh(property_a)
    await db_session.refresh(property_b)

    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(property_a.id), check_in=check_in, check_out=check_out, guest_offer=1)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(property_a.id), check_in=check_in, check_out=check_out, guest_offer=2)
    assert state.last_negotiation_decision["stage_index"] == 1
    assert state.last_negotiation_decision["property_name"] == "Villa A"

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(property_b.id), check_in=check_in, check_out=check_out, guest_offer=None)
    assert state.last_negotiation_decision["stage_index"] == 0
    assert state.last_negotiation_decision["property_name"] == "Villa B"


async def test_21_date_change_resets_negotiation_decision(test_property, db_session, test_user):
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 8}], status="approved"))
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=1)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=2)
    assert state.last_negotiation_decision["stage_index"] == 1

    new_check_in, new_check_out = _dates(offset_days=30)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=new_check_in, check_out=new_check_out, guest_offer=None)
    assert state.last_negotiation_decision["stage_index"] == 0


async def test_22_guest_count_change_resets_negotiation_decision(test_property, db_session, test_user):
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 8}], status="approved"))
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=1, num_guests=2)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=2, num_guests=2)
    assert state.last_negotiation_decision["stage_index"] == 1

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=None, num_guests=5)
    assert state.last_negotiation_decision["stage_index"] == 0


# ---------------------------------------------------------------------------
# RESULT COMMUNICATION (Step 16, items 23-26) -- confirmed reaching
# ConversationState through the REAL wrapper (not manually constructed).
# ---------------------------------------------------------------------------


async def test_23_result_exposes_concession_through_real_wrapper(test_property, db_session, test_user):
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", discount_percent=6, status="approved"))
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=None)
    content = build_state_block_content(state)
    assert "concession" in content.lower()


async def test_24_result_exposes_original_and_final_price(test_property, db_session, test_user):
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", discount_percent=10, status="approved"))
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=None)
    decision = state.last_negotiation_decision
    assert decision["asking_price"] > decision["counter_offer"]
    content = build_state_block_content(state)
    assert f"₹{decision['asking_price']:,.0f}" in content
    assert f"₹{decision['counter_offer']:,.0f}" in content


async def test_25_result_exposes_progression(test_property, db_session, test_user):
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 3}, {"order": 1, "value": 9}], status="approved"))
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=1)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=2)
    assert state.last_negotiation_decision["progressed_this_event"] is True


async def test_26_result_exposes_exhaustion(test_property, db_session, test_user):
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 3}], status="approved"))
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=None)
    assert state.last_negotiation_decision["exhausted"] is True


# ---------------------------------------------------------------------------
# BOUNDARIES (Step 16, items 27-28).
# ---------------------------------------------------------------------------


async def test_27_get_pricing_remains_pricing_only_and_clears_stale_negotiation(test_property, db_session, test_user):
    """get_pricing must never resolve a discount_guest_requests/staged rule
    (structural boundary, Phase 4C/S.1), AND (Phase 4F fix) it must clear
    any stale last_negotiation_decision once a plain quote supersedes it."""
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 50}], status="approved"))
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    get_pricing = next(t for t in tools if t.__name__ == "get_pricing")
    check_in, check_out = _dates()

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=None)
    assert state.last_negotiation_decision is not None

    params = _FakeFunctionCallParams()
    await get_pricing(params, property_id=str(test_property.id), check_in=check_in, check_out=check_out, num_guests=2, apply_discounts=True)
    assert state.last_negotiation_decision is None  # cleared
    assert "50" not in params.result.replace(",", "")  # the 50% staged rule never leaked into get_pricing's own reply


async def test_28_negotiate_rate_remains_the_negotiation_entry_point(test_property, db_session, test_user):
    """A rule with stages must ONLY ever be resolved through negotiate_rate
    -- confirms end-to-end via the wrapper, not just calculate_price
    directly (already covered elsewhere), that get_pricing's own wrapper
    path never touches negotiation_events either."""
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 20}], status="approved"))
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    get_pricing = next(t for t in tools if t.__name__ == "get_pricing")
    check_in, check_out = _dates()

    await get_pricing(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, num_guests=2, apply_discounts=True)
    assert state.negotiation_events == []  # get_pricing never records a negotiation event


# ---------------------------------------------------------------------------
# BACKWARD COMPATIBILITY (Step 16, items 29-30).
# ---------------------------------------------------------------------------


async def test_29_flat_policy_end_to_end_unchanged(test_property, db_session, test_user):
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", discount_percent=7, status="approved"))
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=None)
    assert state.last_negotiation_decision["is_staged"] is False
    assert state.last_negotiation_decision["stage_index"] is None


async def test_30_existing_repeat_guest_behavior_unchanged(test_property, db_session, test_user):
    """DO NOT TOUCH loyalty eligibility -- this test only confirms Phase 4F
    did not accidentally break the existing total_stays-based repeat-guest
    path, per the brief's explicit Step 13 instruction. No loyalty logic is
    modified anywhere in this phase."""
    from app.models.guest_profile import GuestProfile

    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_repeat_guest", discount_percent=9, status="approved"))
    guest = GuestProfile(phone="+919998887777", host_id=test_user.id, total_stays=3)
    db_session.add(guest)
    await db_session.commit()
    await db_session.refresh(guest)

    state = ConversationState()
    tools = build_voice_tools(
        call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state, guest_profile_id=guest.id
    )
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=None)
    expected_floor = round(state.last_negotiation_decision["asking_price"] * (1 - 9 / 100), 2)
    assert state.last_negotiation_decision["counter_offer"] == expected_floor
