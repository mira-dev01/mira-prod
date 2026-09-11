"""Covers the Phase 1 extension to ConversationState (documentation/
agent-conversation-improvement.md) -- slot tracking, recommendation
tracking, escalation/closing flags, and conversation_goal derivation.
Direct dataclass-level tests, separate from test_property_lock.py's
tool-wrapper-level tests (which cover property_id locking specifically).
"""

from app.voice.conversation_state import ConversationState


def test_set_slot_stores_value():
    state = ConversationState()
    state.set_slot("num_guests", 4)
    assert state.slots["num_guests"] == 4


def test_set_slot_ignores_none_and_never_clobbers_existing_field():
    state = ConversationState()
    state.set_slot("num_guests", 6)
    state.set_slot("phone", "9123456789")
    # A later call that only supplies phone must never clobber num_guests --
    # set_slot is called once per field, one at a time, so this models a
    # tool call three turns later that only had a new phone number to report.
    state.set_slot("num_guests", None)
    assert state.slots["num_guests"] == 6
    assert state.slots["phone"] == "9123456789"


def test_record_recommendations_stores_options_and_sets_awaiting_selection():
    state = ConversationState()
    state.record_recommendations(
        [
            {"property_id": "p1", "name": "Ocean View", "price": 6000, "guests": 4},
            {"property_id": "p2", "name": "Palm Retreat", "price": 5000, "guests": 4},
        ]
    )
    assert len(state.recommendations_shown) == 2
    assert state.conversation_goal == "awaiting_selection"


def test_lock_property_after_recommendation_sets_guest_accepted():
    state = ConversationState()
    state.record_recommendations(
        [{"property_id": "p1", "name": "Ocean View", "price": 6000, "guests": 4}]
    )
    state.lock_property("p1", "Ocean View")
    assert state.guest_accepted_property_id == "p1"
    assert state.conversation_goal == "checking_availability"


def test_lock_property_not_previously_recommended_does_not_set_guest_accepted():
    """A property named directly (e.g. Guest Support mode, or a guest naming
    a property that was never shown via recommend_properties) locks fine but
    isn't itself evidence of an accepted recommendation."""
    state = ConversationState()
    state.lock_property("p9", "Some Villa")
    assert state.guest_accepted_property_id is None
    assert state.selected_property_id == "p9"
    # No prior recommendation to have "accepted" -- goal reflects that a
    # property is locked but not yet resolved as an accepted recommendation.
    assert state.conversation_goal == "awaiting_selection"


def test_mark_escalated_sets_flag_and_goal_and_freezes_further_derivation():
    state = ConversationState()
    state.set_slot("num_guests", 4)
    state.mark_escalated()
    assert state.escalated is True
    assert state.conversation_goal == "escalating"
    # Once escalated, later slot/recommendation updates must not silently
    # move the goal away from "escalating" -- the call already needed a
    # human, that's a stronger signal than "still collecting dates".
    state.set_slot("check_in", "2026-08-10")
    assert state.conversation_goal == "escalating"


def test_conversation_goal_derives_from_missing_slots_in_priority_order():
    state = ConversationState()
    # Nothing known yet -- first missing slot in priority order is check_in.
    state.set_slot("purpose_of_stay", "family trip")
    assert state.conversation_goal == "collecting_dates"

    state.set_slot("check_in", "2026-08-10")
    state.set_slot("check_out", "2026-08-12")
    assert state.conversation_goal == "collecting_guests"

    state.set_slot("num_guests", 4)
    # purpose_of_stay already set above, and preferred_location still unset --
    # priority order checks preferred_location before purpose_of_stay, so
    # this should land on collecting_location_or_purpose via preferred_location.
    assert state.conversation_goal == "collecting_location_or_purpose"

    state.set_slot("preferred_location", "Goa")
    # All core slots known, nothing locked/recommended yet -- ready to recommend.
    assert state.conversation_goal == "recommending"


def test_conversation_goal_nights_alone_satisfies_the_dates_gate():
    """A guest who's given a length of stay but no exact check-in/check-out
    yet (e.g. "3 nights sometime in October") has still answered the
    substance of the dates question -- the goal should move on to guests,
    not keep re-deriving collecting_dates every turn."""
    state = ConversationState()
    state.set_slot("purpose_of_stay", "family trip")
    assert state.conversation_goal == "collecting_dates"

    state.set_slot("nights", 3)
    assert state.conversation_goal == "collecting_guests"

    state.set_slot("num_guests", 4)
    assert state.conversation_goal == "collecting_location_or_purpose"

    state.set_slot("preferred_location", "Goa")
    assert state.conversation_goal == "recommending"


def test_conversation_goal_different_real_paths_land_on_different_goals():
    """Two genuinely different conversations (guest gives everything upfront
    vs. one field at a time) must reflect their own real state, not a single
    hardcoded sequence."""
    upfront = ConversationState()
    upfront.set_slot("check_in", "2026-08-10")
    upfront.set_slot("check_out", "2026-08-12")
    upfront.set_slot("num_guests", 4)
    upfront.set_slot("preferred_location", "Goa")
    upfront.set_slot("purpose_of_stay", "friends trip")
    assert upfront.conversation_goal == "recommending"

    slow = ConversationState()
    slow.set_slot("check_in", "2026-08-10")
    assert slow.conversation_goal == "collecting_dates"


def test_closing_state_defaults_open_and_is_a_settable_field():
    state = ConversationState()
    assert state.closing_state == "open"
    state.closing_state = "farewell_pending"
    assert state.closing_state == "farewell_pending"


def test_mark_farewell_pending_sets_state_and_goal():
    state = ConversationState()
    state.set_slot("num_guests", 4)
    state.mark_farewell_pending()
    assert state.closing_state == "farewell_pending"
    assert state.conversation_goal == "closing"


def test_mark_farewell_pending_freezes_further_slot_derived_goal_changes():
    """Same discipline as mark_escalated -- once a close is armed, a later
    slot update must not silently move the goal away from 'closing'."""
    state = ConversationState()
    state.mark_farewell_pending()
    state.set_slot("check_in", "2026-08-10")
    assert state.conversation_goal == "closing"


def test_mark_reopened_resets_closing_state_and_recomputes_goal_from_slots():
    state = ConversationState()
    state.set_slot("check_in", "2026-08-10")
    state.set_slot("check_out", "2026-08-12")
    state.mark_farewell_pending()
    assert state.conversation_goal == "closing"

    state.mark_reopened()
    assert state.closing_state == "open"
    # Recomputed from current known slots, same priority order as normal --
    # num_guests is the next missing one.
    assert state.conversation_goal == "collecting_guests"


def test_mark_closed_sets_terminal_state():
    state = ConversationState()
    state.mark_farewell_pending()
    state.mark_closed()
    assert state.closing_state == "closed"


def test_second_farewell_after_reopen_is_a_fresh_legitimate_close():
    """Reopening a call and later closing it again for real must not be
    treated as a blocked duplicate -- confirms the state-level half of the
    same guarantee test_silence_watchdog.py's integration test covers."""
    state = ConversationState()
    state.mark_farewell_pending()
    state.mark_reopened()
    assert state.closing_state == "open"

    state.mark_farewell_pending()
    assert state.closing_state == "farewell_pending"
    assert state.conversation_goal == "closing"


def test_resolve_cheaper_budget_returns_20_percent_below_cheapest_shown():
    """Recommendation conversations ("Phase X"): "something cheaper" must
    resolve to a real number derived from what was already shown, never an
    LLM-invented figure. Anchors on the CHEAPEST already shown (not the
    average). 20%, not 10% -- self-review fix: filter_builder's own 15%
    budget headroom (`base_price <= budget * 1.15`) is re-applied ON TOP of
    whatever this returns, so the discount here must net below 1.0 after
    that multiply or the cheapest-shown property re-matches itself (a 10%
    discount nets 0.9 * 1.15 = 1.035, ABOVE 1.0 -- the bug this test used to
    encode). 0.8 * 1.15 = 0.92, genuinely below the cheapest shown."""
    state = ConversationState()
    state.record_recommendations(
        [
            {"property_id": "a", "name": "Palm Retreat", "price": 5000, "guests": 4},
            {"property_id": "b", "name": "Ocean View", "price": 6500, "guests": 6},
        ]
    )
    assert state.resolve_cheaper_budget() == 4000.0


def test_resolve_cheaper_budget_none_when_nothing_shown_yet():
    """A guest can technically say "something cheaper" as their very first
    utterance with nothing shown yet -- must fail open to None (the tool
    wrapper falls back to a normal, non-relative search) rather than
    erroring or fabricating a number with nothing real to derive it from."""
    state = ConversationState()
    assert state.resolve_cheaper_budget() is None


def test_resolve_larger_num_guests_returns_one_above_largest_shown():
    """"Something larger" resolves to a real floor derived from the LARGEST
    already shown (not the average) -- +1 is enough to exclude every
    already-shown property from apply_guest_count_filter's own >= check
    while still finding the next size up, not over-shooting."""
    state = ConversationState()
    state.record_recommendations(
        [
            {"property_id": "a", "name": "Palm Retreat", "price": 5000, "guests": 4},
            {"property_id": "b", "name": "Ocean View", "price": 6500, "guests": 6},
        ]
    )
    assert state.resolve_larger_num_guests() == 7


def test_resolve_larger_num_guests_none_when_nothing_shown_yet():
    state = ConversationState()
    assert state.resolve_larger_num_guests() is None


def test_two_conversation_states_are_independent_instances():
    """Confirms no shared/global state -- two concurrent calls must never
    leak into each other (same check memory-architecture-plan.md already ran
    for the original property-lock fields)."""
    state_a = ConversationState()
    state_b = ConversationState()
    state_a.set_slot("num_guests", 4)
    assert "num_guests" not in state_b.slots
    assert state_b.conversation_goal == "greeting"
