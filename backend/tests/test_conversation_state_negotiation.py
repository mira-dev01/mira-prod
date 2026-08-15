"""Phase 4D (generalized negotiation state + policy runtime foundation) --
direct dataclass-level tests for ConversationState.negotiation_events,
record_negotiation_event, and reset_negotiation_context. Separate from
test_conversation_state.py, matching that file's own convention of one
focused file per ConversationState concern (see also
test_conversation_state_attention.py, test_conversation_state_slot_wiring.py).
"""

from app.voice.conversation_state import ConversationState, NegotiationEvent


def test_negotiation_events_starts_empty():
    state = ConversationState()
    assert state.negotiation_events == []


def test_record_negotiation_event_appends():
    state = ConversationState()
    state.record_negotiation_event(4000, "prop-1")
    state.record_negotiation_event(4200, "prop-1")
    assert state.negotiation_events == [
        NegotiationEvent(guest_offer=4000, property_id="prop-1"),
        NegotiationEvent(guest_offer=4200, property_id="prop-1"),
    ]


def test_record_negotiation_event_accepts_none_offer():
    """The unquantified-pushback case (Phase 4C Section E/S.1) -- guest_offer
    can be None, recorded exactly as such, never coerced to a number."""
    state = ConversationState()
    state.record_negotiation_event(None, "prop-1")
    assert state.negotiation_events[0].guest_offer is None


def test_record_negotiation_event_resets_on_property_change():
    """Ratified Phase 4C decision (Section L): a property change is a hard
    negotiation invalidation -- old history for a different property must
    never influence the new property's negotiation."""
    state = ConversationState()
    state.record_negotiation_event(4000, "prop-1")
    state.record_negotiation_event(4200, "prop-1")
    state.record_negotiation_event(3000, "prop-2")  # guest switches property
    assert len(state.negotiation_events) == 1
    assert state.negotiation_events[0] == NegotiationEvent(guest_offer=3000, property_id="prop-2")


def test_reset_negotiation_context_clears_events_only():
    """Ratified Phase 4C decision (Decisions Log item 4): date/guest-count
    change resets negotiation STATE but must never touch conversational
    context (slots, recommendations, goal, etc.)."""
    state = ConversationState()
    state.set_slot("check_in", "2026-09-01")
    state.record_negotiation_event(4000, "prop-1")
    state.lock_property("prop-1", "Test Villa")

    state.reset_negotiation_context()

    assert state.negotiation_events == []
    # Everything else on the dataclass is untouched.
    assert state.slots["check_in"] == "2026-09-01"
    assert state.selected_property_id == "prop-1"
    assert state.selected_property_name == "Test Villa"


def test_reset_negotiation_context_does_not_erase_turn_history_or_attention():
    state = ConversationState()
    state.advance_turn()
    state.advance_turn()
    state.touch_attention("slot:num_guests")
    state.record_negotiation_event(4000, "prop-1")

    state.reset_negotiation_context()

    assert state.turn_index == 2
    assert state.attention_score("slot:num_guests") > 0
    assert state.negotiation_events == []
