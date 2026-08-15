"""Phase 4F (conversation-level negotiation integration) -- covers
_negotiation_hint/build_state_block_content's surfacing of
ConversationState.last_negotiation_decision, and record_negotiation_decision/
clear_negotiation_decision/reset_negotiation_context's own bookkeeping.

Deliberately uses ARBITRARY, VARIED prices/stage counts/values throughout,
per this phase's own no-host-overfitting constraint -- no single price or
stage shape is treated as canonical.
"""

from app.voice.conversation_state import ConversationState
from app.voice.state_prompt_sync import build_state_block_content


def _record(state: ConversationState, **overrides):
    defaults = dict(
        property_name="Test Villa",
        asking_price=10000.0,
        counter_offer=10000.0,
        accepted=True,
        is_staged=False,
        stage_index=None,
        stage_count=None,
        progressed_this_event=False,
        exhausted=False,
        floor_price=10000.0,
    )
    defaults.update(overrides)
    if "floor_price" not in overrides and "counter_offer" in overrides:
        # Most tests only override counter_offer/asking_price without ever
        # thinking about floor_price separately -- for every case in this
        # file, the negotiated counter_offer IS the true floor (guest_offer
        # was None, or the exhausted assertion under test wants
        # counter_offer to genuinely be at the floor). Defaulting floor_price
        # to match counter_offer here keeps every pre-existing call in this
        # file exercising exactly the scenario it was written for, without
        # having to touch every call site individually.
        defaults["floor_price"] = overrides["counter_offer"]
    state.record_negotiation_decision(**defaults)


# ---------------------------------------------------------------------------
# Flat (non-staged) negotiation -- Step 7/8: concession vs. no-concession.
# ---------------------------------------------------------------------------


def test_flat_negotiation_with_real_discount_is_communicated_as_a_concession():
    state = ConversationState()
    _record(state, property_name="Riverside Cottage", asking_price=7200.0, counter_offer=6480.0)
    content = build_state_block_content(state)
    assert "₹7,200" in content
    assert "₹6,480" in content
    assert "concession" in content.lower()
    # No percentage anywhere -- the fact is expressed in real rupee amounts,
    # never a host-specific percent this function has no business inventing.
    assert "%" not in content


def test_flat_negotiation_with_no_discount_does_not_claim_a_concession():
    """guest_offer >= floor_price accepted at face value -- counter_offer
    equals asking_price (or is not meaningfully lower), so no concession
    language should appear."""
    state = ConversationState()
    _record(state, property_name="Hilltop Bungalow", asking_price=5000.0, counter_offer=5000.0)
    content = build_state_block_content(state)
    assert "₹5,000" in content
    assert "no discount was applied" in content.lower() or "not imply a further concession" in content.lower()


def test_flat_negotiation_arbitrary_large_values():
    """Arbitrary, non-round, larger values -- proves nothing is hardcoded to
    a specific price magnitude."""
    state = ConversationState()
    _record(state, property_name="Palace Suite", asking_price=184500.0, counter_offer=171000.75)
    content = build_state_block_content(state)
    assert "₹184,500" in content
    assert "₹171,001" in content or "₹171,000" in content  # rounding via :,.0f


# ---------------------------------------------------------------------------
# Staged negotiation -- Step 9: progression, no-progression, exhaustion.
# Arbitrary stage counts/values throughout (2, 3, 5, 7 stages tested).
# ---------------------------------------------------------------------------


def test_staged_progression_communicates_improved_offer():
    state = ConversationState()
    _record(
        state,
        property_name="Lakeview Cabin",
        asking_price=9000.0,
        counter_offer=8100.0,
        is_staged=True,
        stage_index=1,
        stage_count=3,
        progressed_this_event=True,
        exhausted=False,
    )
    content = build_state_block_content(state)
    assert "better" in content.lower() or "improve" in content.lower()
    assert "₹8,100" in content
    # No raw stage-number language exposed to the guest-facing hint --
    # guests don't think in "stage 1 of 3".
    assert "stage 1" not in content.lower()
    assert "stage_index" not in content


def test_staged_non_progression_does_not_claim_a_new_concession():
    """A repeated/non-qualifying pushback -- progressed_this_event=False,
    not exhausted -- must not be phrased as a new or better offer."""
    state = ConversationState()
    _record(
        state,
        property_name="Garden Villa",
        asking_price=6000.0,
        counter_offer=5700.0,
        is_staged=True,
        stage_index=0,
        stage_count=4,
        progressed_this_event=False,
        exhausted=False,
    )
    content = build_state_block_content(state)
    assert "not a new or improved concession" in content.lower()
    assert "₹5,700" in content


def test_staged_exhaustion_communicates_final_price_with_no_further_room():
    state = ConversationState()
    _record(
        state,
        property_name="Beachfront Villa",
        asking_price=15000.0,
        counter_offer=12000.0,
        is_staged=True,
        stage_index=2,
        stage_count=3,
        progressed_this_event=False,
        exhausted=True,
    )
    content = build_state_block_content(state)
    assert "maximum" in content.lower() or "best price" in content.lower() or "no further concession" in content.lower()
    assert "₹12,000" in content
    assert "do not invent an additional discount" in content.lower()


def test_staged_exhaustion_but_accepted_above_true_floor_does_not_falsely_claim_no_room_left():
    """Self-review regression: pricing_engine.negotiate_rate's accepted
    branch sets counter_offer to the GUEST's own offer, which can be
    strictly above the true floor_price even when the stage ladder has
    reached its last rung (exhausted=True). Confirmed reachable via a
    direct probe against negotiate_rate with a real 2-stage policy: a
    guest offer of floor_price(stage1) + 500, still above the prior
    stage's own floor, landed exactly here -- exhausted=True, accepted=True,
    counter_offer > floor_price. The hint must NOT claim "this is the
    maximum you're authorized to offer" in that case, since real room to
    go lower still exists; it must not claim a genuinely new/better
    concession either, since nothing about the guest's own accepted offer
    represents a host-authorized improvement."""
    state = ConversationState()
    _record(
        state,
        property_name="Ridge House",
        asking_price=8000.0,
        counter_offer=7700.0,
        floor_price=6400.0,  # the true stage-1 floor -- well below counter_offer
        accepted=True,
        is_staged=True,
        stage_index=1,
        stage_count=2,
        progressed_this_event=True,
        exhausted=True,
    )
    content = build_state_block_content(state)
    assert "₹7,700" in content
    assert "maximum" not in content.lower()
    assert "no further concession" not in content.lower()
    assert "do not invent an additional discount" not in content.lower()


def test_staged_exhaustion_at_true_floor_still_communicates_no_further_room():
    """Companion to the regression above -- the common real case (guest
    asks "what's your best price", guest_offer=None, counter_offer is
    ALWAYS floor_price on that path) must still get the "no further room"
    framing once exhausted. Proves the at_true_floor gate doesn't silently
    swallow the legitimate exhausted case, only the false-positive one."""
    state = ConversationState()
    _record(
        state,
        property_name="Ridge House",
        asking_price=8000.0,
        counter_offer=6400.0,
        floor_price=6400.0,  # counter_offer IS the floor -- the guest-offer=None path
        accepted=True,
        is_staged=True,
        stage_index=1,
        stage_count=2,
        progressed_this_event=False,
        exhausted=True,
    )
    content = build_state_block_content(state)
    assert "₹6,400" in content
    assert "maximum" in content.lower() or "best price" in content.lower() or "no further concession" in content.lower()
    assert "do not invent an additional discount" in content.lower()


def test_staged_arbitrary_five_stage_policy():
    """5-stage policy, arbitrary values -- proves the hint logic makes no
    assumption about a specific ladder length."""
    state = ConversationState()
    _record(
        state,
        property_name="Countryside Retreat",
        asking_price=4400.0,
        counter_offer=3960.0,
        is_staged=True,
        stage_index=3,
        stage_count=5,
        progressed_this_event=True,
        exhausted=False,
    )
    content = build_state_block_content(state)
    assert "₹3,960" in content
    assert "stage_count" not in content
    assert "5" not in content.replace("₹3,960", "").replace(",", "")  # stage count itself never leaks as a bare digit


def test_staged_arbitrary_seven_stage_policy_final_stage():
    state = ConversationState()
    _record(
        state,
        property_name="Mountain Lodge",
        asking_price=8800.0,
        counter_offer=6600.0,
        is_staged=True,
        stage_index=6,
        stage_count=7,
        progressed_this_event=True,
        exhausted=True,
    )
    content = build_state_block_content(state)
    assert "₹6,600" in content
    assert "maximum" in content.lower() or "best price" in content.lower() or "no further concession" in content.lower()


# ---------------------------------------------------------------------------
# No negotiation yet -- must be a true no-op (Step 7's "backend owns the
# numbers" only applies once a negotiation has actually happened).
# ---------------------------------------------------------------------------


def test_no_hint_when_no_negotiation_happened_yet():
    state = ConversationState()
    content = build_state_block_content(state)
    assert content == ""
    assert state.last_negotiation_decision is None


# ---------------------------------------------------------------------------
# clear_negotiation_decision / precedence with quoted_price (Step 6B fix).
# ---------------------------------------------------------------------------


def test_clear_negotiation_decision_removes_the_fact():
    state = ConversationState()
    _record(state)
    assert state.last_negotiation_decision is not None
    state.clear_negotiation_decision()
    assert state.last_negotiation_decision is None
    assert build_state_block_content(state) == "" or "negotiat" not in build_state_block_content(state).lower()


def test_negotiation_hint_supersedes_generic_quoted_price_hint():
    """When the most recent price event was a negotiation, the negotiation
    hint (with its concession-aware framing) should be the one line shown,
    not the generic "you already quoted" line -- both would otherwise
    describe the exact same number redundantly."""
    state = ConversationState()
    state.record_quoted_price("Sunset Villa", "2026-09-01", "2026-09-03", 9500.0)
    _record(state, property_name="Sunset Villa", asking_price=10000.0, counter_offer=9500.0)
    content = build_state_block_content(state)
    assert "You already quoted" not in content  # superseded
    assert "concession" in content.lower() or "already confirmed" in content.lower()


def test_plain_get_pricing_quote_after_negotiation_supersedes_the_stale_decision():
    """Simulates the exact sequence get_pricing's wrapper produces: a
    negotiation happens, then a LATER plain get_pricing call clears
    last_negotiation_decision (ConversationState.clear_negotiation_decision) --
    the generic quoted_price hint becomes correct again, and the stale
    negotiation hint must not still be shown."""
    state = ConversationState()
    _record(state, property_name="Ocean Breeze", asking_price=7000.0, counter_offer=6300.0)
    assert state.last_negotiation_decision is not None

    # A later plain quote arrives (e.g. guest asks about different dates,
    # not a negotiation) -- mirrors get_pricing's own _on_priced callback.
    state.record_quoted_price("Ocean Breeze", "2026-10-01", "2026-10-04", 7500.0)
    state.clear_negotiation_decision()

    content = build_state_block_content(state)
    assert "You already quoted ₹7,500" in content
    assert "concession" not in content.lower()


# ---------------------------------------------------------------------------
# reset_negotiation_context also clears last_negotiation_decision (Step 11).
# ---------------------------------------------------------------------------


def test_reset_negotiation_context_clears_last_negotiation_decision_too():
    state = ConversationState()
    _record(state, property_name="Property A", asking_price=5000.0, counter_offer=4500.0)
    assert state.last_negotiation_decision is not None

    state.reset_negotiation_context()

    assert state.last_negotiation_decision is None
    assert state.negotiation_events == []


def test_reset_negotiation_context_does_not_touch_unrelated_state():
    state = ConversationState()
    state.set_slot("num_guests", 3)
    state.lock_property("prop-1", "Property A")
    _record(state)

    state.reset_negotiation_context()

    assert state.slots["num_guests"] == 3
    assert state.selected_property_id == "prop-1"
    assert state.last_negotiation_decision is None
