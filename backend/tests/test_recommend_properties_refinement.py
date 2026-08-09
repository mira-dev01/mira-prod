"""Covers Recommendation conversations ("Phase X",
documentation/agent-conversation-improvement.md): the recommend_properties
tool wrapper's (app/voice/tools.py) refinement-specific behavior --
preferred_location/purpose_of_stay backfill-on-omission, required_amenities
accumulation (merge, never replace), and cheaper_than_shown/
larger_than_shown resolving to real numbers derived from
ConversationState.recommendations_shown. Exercised at the actual tool-call
level, separate from test_conversation_state.py's isolated resolver-method
tests and test_property_retrieval_filter_builder.py's isolated boost-function
tests.
"""

import uuid

from app.models.property import Property
from app.voice.conversation_state import ConversationState
from app.voice.tools import build_voice_tools


class _FakeFunctionCallParams:
    def __init__(self):
        self.result = None

    async def result_callback(self, result):
        self.result = result


async def _property(db_session, test_user, **overrides):
    defaults = dict(
        user_id=test_user.id,
        name="Test Villa",
        city="Goa",
        exophone=f"+9180{uuid.uuid4().int % 10**8:08d}",
        base_price=3000,
        max_guests=2,
        neighborhood_info="",
        amenity_tags=[],
    )
    defaults.update(overrides)
    property_ = Property(**defaults)
    db_session.add(property_)
    await db_session.commit()
    await db_session.refresh(property_)
    return property_


async def test_preferred_location_backfilled_from_state_when_omitted(db_session, test_user):
    """A follow-up call that omits preferred_location must still narrow by
    the location given earlier this conversation, not silently search the
    whole portfolio again."""
    await _property(db_session, test_user, name="Goa Villa", city="Goa")
    await _property(db_session, test_user, name="Jaipur Haveli", city="Jaipur")

    state = ConversationState()
    state.set_slot("preferred_location", "Goa")
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    params = _FakeFunctionCallParams()
    await recommend_properties(params)
    assert "Goa Villa" in params.result
    assert "Jaipur Haveli" not in params.result


async def test_purpose_of_stay_backfilled_from_state_when_omitted(db_session, test_user):
    await _property(db_session, test_user, name="Solo Villa")
    state = ConversationState()
    state.set_slot("purpose_of_stay", "workcation")
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    params = _FakeFunctionCallParams()
    await recommend_properties(params)
    assert state.slots["purpose_of_stay"] == "workcation"


async def test_explicit_location_this_call_overrides_and_updates_state(db_session, test_user):
    """An explicit new value always wins over backfill and is itself saved,
    so the NEXT call's backfill reflects the guest's latest stated criteria."""
    await _property(db_session, test_user, name="Goa Villa", city="Goa")
    await _property(db_session, test_user, name="Jaipur Haveli", city="Jaipur")

    state = ConversationState()
    state.set_slot("preferred_location", "Goa")
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    params = _FakeFunctionCallParams()
    await recommend_properties(params, preferred_location="Jaipur")
    assert "Jaipur Haveli" in params.result
    assert state.slots["preferred_location"] == "Jaipur"


async def test_required_amenities_accumulate_across_calls_not_replace(db_session, test_user):
    """A guest asking for 'pool' then later 'pet friendly' means BOTH,
    per explicit product direction -- the second call's required_amenities
    must merge with, not replace, what was requested earlier."""
    await _property(db_session, test_user, name="Both Villa", amenity_tags=["pool", "pets_allowed"])
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    params = _FakeFunctionCallParams()
    await recommend_properties(params, required_amenities=["pool"])
    assert state.slots["required_amenities"] == ["pool"]

    params2 = _FakeFunctionCallParams()
    await recommend_properties(params2, required_amenities=["pet friendly"])
    assert set(state.slots["required_amenities"]) == {"pet friendly", "pool"}


async def test_recommend_properties_touches_attention_for_newly_mentioned_amenities_only(db_session, test_user):
    """Attention/salience: only the amenities actually present in THIS
    call's raw required_amenities argument get touched -- the accumulated
    list must not re-touch earlier amenities on every subsequent call (see
    ConversationState.touch_attention's callers in tools.py)."""
    await _property(db_session, test_user, name="Villa")
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    await recommend_properties(_FakeFunctionCallParams(), required_amenities=["pool"])
    assert state.attention["amenity:pool"].count == 1

    # A later call that repeats "pool" (still the guest's stated need) AND
    # adds a new amenity -- "pool" should be touched again (mentioned twice
    # now), "pet friendly" freshly touched once.
    await recommend_properties(_FakeFunctionCallParams(), required_amenities=["pool", "pet friendly"])
    assert state.attention["amenity:pool"].count == 2
    assert state.attention["amenity:pets_allowed"].count == 1


async def test_recommend_properties_amenity_attention_breaks_a_match_count_tie_by_emphasis(
    db_session, test_user
):
    """End-to-end: two properties each match exactly ONE of the two
    requested amenities -- a flat match-count boost can't distinguish them
    at all (a tie, so the underlying price-ascending SQL order would just
    win by default). A guest who's asked about "pool" three separate times
    but "wifi" only once should see the pool-only property ranked first
    despite being pricier -- confirms amenity_weights actually threads all
    the way through tools.py -> tool_handlers -> orchestrator -> sql_search
    -> filter_builder.apply_amenity_boost, not just that it's computed.
    (Matching MORE amenities always still wins outright over matching
    fewer, by design -- attention only ever breaks a tie in match count,
    never overrides it; see filter_builder.apply_amenity_boost's own
    docstring.)"""
    await _property(db_session, test_user, name="Wifi Villa", amenity_tags=["wifi"], base_price=3000)
    await _property(db_session, test_user, name="Pool Villa", amenity_tags=["pool"], base_price=6000)
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    # Guest asks about the pool three separate times across the call.
    await recommend_properties(_FakeFunctionCallParams(), required_amenities=["pool"])
    await recommend_properties(_FakeFunctionCallParams(), required_amenities=["pool"])
    await recommend_properties(_FakeFunctionCallParams(), required_amenities=["pool"])
    final = _FakeFunctionCallParams()
    await recommend_properties(final, required_amenities=["pool", "wifi"])

    pool_pos = final.result.index("Pool Villa")
    wifi_pos = final.result.index("Wifi Villa")
    assert pool_pos < wifi_pos


async def test_amenity_soft_match_still_returns_property_missing_one_requested_amenity(db_session, test_user):
    """The retrieval-semantics change this phase made: required_amenities is
    now a soft boost, so a property with only SOME of the accumulated
    amenities is still returned (never zero-results), with the checklist
    spoken explicitly."""
    await _property(db_session, test_user, name="PoolOnly Villa", amenity_tags=["pool"])
    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    params = _FakeFunctionCallParams()
    await recommend_properties(params, required_amenities=["pool", "pet friendly"])
    assert "PoolOnly Villa" in params.result
    assert "has pool but not pet friendly" in params.result.lower()


async def test_cheaper_than_shown_resolves_to_real_budget_and_excludes_cheapest_shown(db_session, test_user):
    """'Something cheaper' must resolve via ConversationState.resolve_cheaper_
    budget (20% below the cheapest already-shown property -- enough to net
    below the cheapest shown price even after build_base_filters' own 15%
    budget headroom is re-applied on top), never an LLM-invented number --
    confirmed here by NOT passing budget at all, and by confirming the
    cheapest ALREADY-shown property itself does not re-match."""
    cheap = await _property(db_session, test_user, name="Cheap Villa", base_price=3000)
    pricey = await _property(db_session, test_user, name="Pricey Villa", base_price=8000)

    state = ConversationState()
    state.record_recommendations(
        [
            {"property_id": str(cheap.id), "name": cheap.name, "price": 3000, "guests": 2},
            {"property_id": str(pricey.id), "name": pricey.name, "price": 8000, "guests": 2},
        ]
    )
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    even_cheaper = await _property(db_session, test_user, name="Even Cheaper Villa", base_price=2000)

    params = _FakeFunctionCallParams()
    await recommend_properties(params, cheaper_than_shown=True)
    assert "Even Cheaper Villa" in params.result
    assert "Cheap Villa" not in params.result  # the cheapest ALREADY shown must not re-match itself


async def test_cheaper_than_shown_derived_budget_is_not_persisted_to_state(db_session, test_user):
    """The resolved 'cheaper than shown' budget is a one-off number for THIS
    search only -- it must NOT be written to state.slots, or a later,
    unrelated turn that omits budget would silently inherit it as if the
    guest had actually stated that number."""
    cheap = await _property(db_session, test_user, name="Cheap Villa", base_price=3000)
    state = ConversationState()
    state.record_recommendations(
        [{"property_id": str(cheap.id), "name": cheap.name, "price": 3000, "guests": 2}]
    )
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    params = _FakeFunctionCallParams()
    await recommend_properties(params, cheaper_than_shown=True)
    assert "budget" not in state.slots

    # A later, unrelated call omitting budget must see an unfiltered search,
    # not the derived "cheaper than shown" ceiling from the earlier turn.
    pricier = await _property(db_session, test_user, name="Later Villa", base_price=9000)
    params2 = _FakeFunctionCallParams()
    await recommend_properties(params2, preferred_location=pricier.city)
    assert "Later Villa" in params2.result


async def test_larger_than_shown_resolves_to_real_guest_count_from_recommendations_shown(db_session, test_user):
    """'Something larger' resolves via resolve_larger_num_guests (one above
    the largest already-shown capacity), never an LLM-invented number."""
    shown = await _property(db_session, test_user, name="Shown Villa", max_guests=4)
    state = ConversationState()
    state.record_recommendations(
        [{"property_id": str(shown.id), "name": shown.name, "price": 5000, "guests": 4}]
    )
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    bigger = await _property(db_session, test_user, name="Bigger Villa", max_guests=6)

    params = _FakeFunctionCallParams()
    await recommend_properties(params, larger_than_shown=True)
    assert "Bigger Villa" in params.result
    assert "num_guests" not in state.slots  # derived value must not be persisted


async def test_explicit_budget_wins_over_cheaper_than_shown_flag(db_session, test_user):
    """An explicit absolute value given the SAME call is a stronger signal
    than a relative flag and must win outright, even if both are somehow
    set together."""
    cheap = await _property(db_session, test_user, name="Cheap Villa", base_price=3000)
    state = ConversationState()
    state.record_recommendations(
        [{"property_id": str(cheap.id), "name": cheap.name, "price": 3000, "guests": 2}]
    )
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    params = _FakeFunctionCallParams()
    await recommend_properties(params, cheaper_than_shown=True, budget=9999)
    assert state.slots["budget"] == 9999


async def test_more_premium_than_shown_ranks_premium_property_first(db_session, test_user):
    await _property(db_session, test_user, name="Regular Villa", is_premium=False, base_price=3000)
    await _property(db_session, test_user, name="Premium Villa", is_premium=True, base_price=3100)

    state = ConversationState()
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    params = _FakeFunctionCallParams()
    await recommend_properties(params, more_premium_than_shown=True)
    assert params.result.index("Premium Villa") < params.result.index("Regular Villa")


async def test_relative_refinement_flags_count_as_new_criteria_for_lock_backstop(db_session, test_user):
    """The property-lock backstop (Fix B) must not block a refinement call
    that supplies ONLY a relative flag and nothing else -- cheaper_than_shown/
    larger_than_shown/more_premium_than_shown are themselves new criteria,
    same as an explicit location/budget would be."""
    locked = await _property(db_session, test_user, name="Locked Villa", base_price=5000)
    await _property(db_session, test_user, name="Cheaper Villa", base_price=2000)

    state = ConversationState()
    state.record_recommendations(
        [{"property_id": str(locked.id), "name": locked.name, "price": 5000, "guests": 2}]
    )
    state.lock_property(str(locked.id), locked.name)
    tools = build_voice_tools(call_session_id=None, property_id=None, host_user_id=test_user.id, conversation_state=state)
    recommend_properties = next(t for t in tools if t.__name__ == "recommend_properties")

    params = _FakeFunctionCallParams()
    await recommend_properties(params, cheaper_than_shown=True)
    assert "already looking at" not in params.result.lower()
