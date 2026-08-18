"""Phase 4D -- wrapper-level tests for app/voice/tools.py's negotiate_rate
closure: negotiation-event recording, date/guest-count context reset, and
the abuse/failure scenarios from the Phase 4D brief's Step 15 (repeated
"can you do better?", duplicate tool calls, property change mid-negotiation,
repeat guest with a staged policy, no applicable policy). Modeled directly
on tests/test_voice_tools.py's own _FakeFunctionCallParams pattern -- no
negotiate_rate wrapper test existed before this phase.
"""

from datetime import date, timedelta

from app.models.guest_profile import GuestProfile
from app.models.negotiation_rule import NegotiationRule
from app.models.property import Property
from app.voice.conversation_state import ConversationState, NegotiationEvent
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


async def test_wrapper_records_negotiation_event_after_each_call(test_property, db_session, test_user):
    state = ConversationState()
    tools = build_voice_tools(
        call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state
    )
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=4000)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=4200)

    assert state.negotiation_events == [
        NegotiationEvent(guest_offer=4000, property_id=str(test_property.id)),
        NegotiationEvent(guest_offer=4200, property_id=str(test_property.id)),
    ]


async def test_wrapper_records_none_for_unquantified_pushback(test_property, db_session, test_user):
    state = ConversationState()
    tools = build_voice_tools(
        call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state
    )
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out)
    assert state.negotiation_events == [NegotiationEvent(guest_offer=None, property_id=str(test_property.id))]


# ---------------------------------------------------------------------------
# Abuse case 1: repeated "can you do better?" (guest_offer left unset each
# time) does not automatically burn through all stages.
# ---------------------------------------------------------------------------


async def test_abuse_1_repeated_unquantified_pushback_does_not_burn_stages(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}, {"order": 2, "value": 15}],
            status="approved",
        )
    )
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(
        call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state
    )
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    for _ in range(5):
        await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out)

    # 5 repeated "can you do better?" calls -- still only ever stage 0.
    assert len(state.negotiation_events) == 5
    assert all(e.guest_offer is None for e in state.negotiation_events)


# ---------------------------------------------------------------------------
# Abuse case 2/3/4: same offer repeated, lower offer, strictly higher offer.
# ---------------------------------------------------------------------------


async def test_abuse_2_repeated_identical_offer_does_not_progress(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}], status="approved"
        )
    )
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    from app.services import negotiation_policy

    params1 = _FakeFunctionCallParams()
    await negotiate_rate(params1, property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=4000)
    params2 = _FakeFunctionCallParams()
    await negotiate_rate(params2, property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=4000)

    assert len(state.negotiation_events) == 2
    # The repeated identical offer never progressed the derived stage index.
    assert negotiation_policy.resolve_stage_index(state.negotiation_events, 2) == 0


async def test_abuse_5_duplicate_tool_call_same_offer_cannot_consume_another_stage(test_property, db_session, test_user):
    """Simulates noisy/background speech producing a duplicate tool call --
    two identical negotiate_rate calls back to back must not authorize more
    than a single call with that offer would have."""
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}, {"order": 2, "value": 15}],
            status="approved",
        )
    )
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=4500)
    # Duplicate call, identical args, right after -- as if two
    # FunctionCallsStartedFrame entries fired for the same utterance.
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=4500)

    from app.services import negotiation_policy

    stage_after_both = negotiation_policy.resolve_stage_index(state.negotiation_events, 3)
    stage_after_one = negotiation_policy.resolve_stage_index(state.negotiation_events[:1], 3)
    assert stage_after_both == stage_after_one  # the duplicate call added nothing


# ---------------------------------------------------------------------------
# Abuse case 6: guest changes property mid-negotiation.
# ---------------------------------------------------------------------------


async def test_abuse_6_property_change_invalidates_old_negotiation_history(db_session, test_user):
    property_a = Property(user_id=test_user.id, name="Villa A", city="Goa", exophone="+918000000010", base_price=4000, max_guests=4)
    property_b = Property(user_id=test_user.id, name="Villa B", city="Goa", exophone="+918000000011", base_price=4000, max_guests=4)
    db_session.add_all([property_a, property_b])
    db_session.add(
        NegotiationRule(
            host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}], status="approved"
        )
    )
    await db_session.commit()
    await db_session.refresh(property_a)
    await db_session.refresh(property_b)

    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(property_a.id), check_in=check_in, check_out=check_out, guest_offer=1000)
    # Second, strictly-higher offer on property A -- progresses to stage 1 (9%).
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(property_a.id), check_in=check_in, check_out=check_out, guest_offer=1200)
    # Guest switches to property B -- this is the FIRST call on B, and must
    # be evaluated at stage 0 (4%), never inheriting property A's stage 1.
    params = _FakeFunctionCallParams()
    await negotiate_rate(params, property_id=str(property_b.id), check_in=check_in, check_out=check_out, guest_offer=None)

    assert len(state.negotiation_events) == 1  # old property A history discarded
    assert state.negotiation_events[0].property_id == str(property_b.id)

    # Self-review regression: confirms the RESOLVED PRICE for property B's
    # first call actually used stage 0 (4%), not property A's stage 1 (9%)
    # -- checking negotiation_events alone (above) is not sufficient, since
    # an earlier, buggy implementation cleared the stale history only AFTER
    # this call had already resolved against it, which state.negotiation_events'
    # own post-hoc value could never reveal.
    from app.services.pricing_engine import calculate_price

    breakdown = await calculate_price(db_session, property_b, date.fromisoformat(check_in), date.fromisoformat(check_out), apply_discounts=False)
    price_at_stage_0 = round(breakdown.total * (1 - 4 / 100), 2)
    price_at_stage_1 = round(breakdown.total * (1 - 9 / 100), 2)
    assert f"{price_at_stage_0:,.0f}" in params.result
    assert f"{price_at_stage_1:,.0f}" not in params.result


# ---------------------------------------------------------------------------
# Abuse case 7: repeat guest with staged policy -- repeat-guest precedence
# still applies (verified at the pricing_engine layer in
# test_negotiate_rate_staged_policy.py's test_n; here confirmed the wrapper
# doesn't interfere with that resolution).
# ---------------------------------------------------------------------------


async def test_abuse_7_repeat_guest_with_staged_policy_still_gets_repeat_guest_precedence(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 3}], status="approved")
    )
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_repeat_guest", discount_percent=14, status="approved"))
    guest = GuestProfile(host_id=test_user.id, phone="+919990002222", total_stays=5)
    db_session.add(guest)
    await db_session.commit()
    await db_session.refresh(guest)

    state = ConversationState()
    tools = build_voice_tools(
        call_session_id=None,
        property_id=test_property.id,
        host_user_id=test_user.id,
        conversation_state=state,
        guest_profile_id=guest.id,
    )
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    params = _FakeFunctionCallParams()
    await negotiate_rate(params, property_id=str(test_property.id), check_in=check_in, check_out=check_out)

    # Directly verify against pricing_engine what the resolved price at 14%
    # (repeat-guest) vs. 3% (staged guest_requests) would each be, then
    # confirm the wrapper's spoken result names the 14% price, not the 3%
    # one -- a precise check, not a fragile substring guess.
    from app.services.pricing_engine import calculate_price

    breakdown = await calculate_price(db_session, test_property, date.fromisoformat(check_in), date.fromisoformat(check_out), apply_discounts=False)
    price_at_14_percent = round(breakdown.total * (1 - 14 / 100), 2)
    price_at_3_percent = round(breakdown.total * (1 - 3 / 100), 2)
    assert f"{price_at_14_percent:,.0f}" in params.result
    assert f"{price_at_3_percent:,.0f}" not in params.result


# ---------------------------------------------------------------------------
# Abuse case 8: no applicable policy -- existing fallback unchanged.
# ---------------------------------------------------------------------------


async def test_abuse_8_no_applicable_policy_existing_fallback_unchanged(test_property, db_session, test_user):
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    params = _FakeFunctionCallParams()
    await negotiate_rate(params, property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=1)
    assert params.result is not None  # existing fallback (loyalty-bonus formula) still produces a real response


# ---------------------------------------------------------------------------
# Date/guest-count context reset (ratified Phase 4C decision).
# ---------------------------------------------------------------------------


async def test_date_change_resets_negotiation_events_but_keeps_other_state(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}], status="approved"
        )
    )
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=1000)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=1200)
    assert len(state.negotiation_events) == 2

    new_check_in, new_check_out = _dates(offset_days=20)  # guest changes their mind about dates
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=new_check_in, check_out=new_check_out, guest_offer=None)

    # Negotiation state reset -- only the new call's own event remains.
    assert len(state.negotiation_events) == 1
    # Conversational context (the property lock) is retained, not erased.
    assert state.selected_property_id == str(test_property.id)


async def test_guest_count_change_resets_negotiation_events(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}], status="approved"
        )
    )
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=1000, num_guests=2)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=1200, num_guests=2)
    assert len(state.negotiation_events) == 2

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=None, num_guests=4)
    assert len(state.negotiation_events) == 1


async def test_same_dates_and_guest_count_does_not_reset(test_property, db_session, test_user):
    """Negative case -- calling again with the SAME dates/guest count must
    NOT reset (that would defeat progression entirely)."""
    db_session.add(
        NegotiationRule(
            host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}], status="approved"
        )
    )
    await db_session.commit()
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=test_property.id, host_user_id=test_user.id, conversation_state=state)
    negotiate_rate = next(t for t in tools if t.__name__ == "negotiate_rate")
    check_in, check_out = _dates()

    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=1000, num_guests=2)
    await negotiate_rate(_FakeFunctionCallParams(), property_id=str(test_property.id), check_in=check_in, check_out=check_out, guest_offer=1200, num_guests=2)
    assert len(state.negotiation_events) == 2  # both retained -- no reset triggered
