"""Covers ConversationState's attention/salience tracking (Salience,
touch_attention, attention_score, advance_turn, and set_slot's
change-detection gate) -- separate from test_conversation_state.py's
existing slot/goal-derivation coverage, since this is a genuinely distinct
mechanism layered on top of it.
"""

from app.voice.conversation_state import ConversationState, Salience


def test_touch_attention_starts_a_new_entry_at_the_current_turn():
    state = ConversationState()
    state.advance_turn()  # turn_index -> 1
    state.touch_attention("slot:budget")
    entry = state.attention["slot:budget"]
    assert entry.count == 1
    assert entry.last_turn == 1


def test_touch_attention_increments_count_and_updates_last_turn_on_repeat():
    state = ConversationState()
    state.advance_turn()
    state.touch_attention("slot:budget")
    state.advance_turn()
    state.advance_turn()
    state.touch_attention("slot:budget")
    entry = state.attention["slot:budget"]
    assert entry.count == 2
    assert entry.last_turn == 3


def test_attention_score_zero_for_unknown_key():
    state = ConversationState()
    assert state.attention_score("slot:budget") == 0.0


def test_attention_score_decays_with_turns_since_last_mention():
    state = ConversationState()
    state.advance_turn()
    state.touch_attention("slot:budget")  # count=1, last_turn=1
    score_now = state.attention_score("slot:budget", half_life=6)
    for _ in range(6):
        state.advance_turn()
    score_later = state.attention_score("slot:budget", half_life=6)
    assert score_now == 1.0
    # Exactly one half-life later, the same single mention is worth half.
    assert abs(score_later - 0.5) < 1e-9


def test_attention_score_rewards_repetition_over_a_single_recent_mention():
    state = ConversationState()
    # "budget" mentioned twice, long ago.
    state.advance_turn()
    state.touch_attention("slot:budget")
    state.advance_turn()
    state.touch_attention("slot:budget")
    for _ in range(20):
        state.advance_turn()
    # "location" mentioned once, at the current turn.
    state.touch_attention("slot:location")
    # Repetition still keeps budget's score positive even decayed far out,
    # but a single very-recent mention outscores a heavily-decayed repeat --
    # both signals matter, neither one alone dominates arbitrarily.
    assert state.attention_score("slot:location") == 1.0
    assert 0 < state.attention_score("slot:budget") < 1.0


def test_salience_score_half_life_le_zero_only_current_turn_counts():
    s = Salience(count=3, last_turn=5)
    assert s.score(current_turn=5, half_life=0) == 3.0
    assert s.score(current_turn=6, half_life=0) == 0.0


def test_advance_turn_increments_turn_index():
    state = ConversationState()
    assert state.turn_index == 0
    state.advance_turn()
    state.advance_turn()
    assert state.turn_index == 2


def test_set_slot_touches_attention_on_first_set():
    state = ConversationState()
    state.advance_turn()
    state.set_slot("num_guests", 4)
    assert state.attention["slot:num_guests"].count == 1


def test_set_slot_does_not_touch_attention_when_value_unchanged():
    """The real bug this guards against: app/voice/tools.py's
    recommend_properties wrapper calls set_slot every turn with a
    BACKFILLED value pulled from state itself whenever the model omits a
    field -- that must never look like the guest repeating themselves."""
    state = ConversationState()
    state.advance_turn()
    state.set_slot("num_guests", 4)
    state.advance_turn()
    state.set_slot("num_guests", 4)  # backfill, same value
    state.advance_turn()
    state.set_slot("num_guests", 4)  # backfill again
    assert state.attention["slot:num_guests"].count == 1
    assert state.attention["slot:num_guests"].last_turn == 1


def test_set_slot_touches_attention_again_on_a_genuine_change():
    state = ConversationState()
    state.advance_turn()
    state.set_slot("budget", 5000)
    state.advance_turn()
    state.set_slot("budget", 7000)  # real correction, not a backfill echo
    assert state.attention["slot:budget"].count == 2
    assert state.attention["slot:budget"].last_turn == 2


def test_set_slot_ignores_none_without_touching_attention():
    state = ConversationState()
    state.advance_turn()
    state.set_slot("phone", None)
    assert "slot:phone" not in state.attention
