"""The host's must-have details (voice/collect_gate.py), as pure policy."""

from app.schemas.agent_config import AgentConfig
from app.voice import collect_gate
from app.voice.conversation_state import ConversationState


def _config(**collect) -> AgentConfig:
    return AgentConfig.model_validate({"collect": collect})


def test_default_host_needs_nothing_to_recommend_but_name_and_phone_to_price():
    state, config = ConversationState(), AgentConfig()
    assert collect_gate.evaluate(state, config, "recommend").block is False
    decision = collect_gate.evaluate(state, config, "funnel")
    assert decision.block and decision.ask_for == ["guest_name", "phone"]


def test_name_and_phone_cannot_be_turned_off():
    # Even a config that sets every collect field off still asks for these.
    config = _config(dates="off", guests="off", purpose="off", location="off", budget="off", email="off")
    assert collect_gate.required_details(config, "funnel") == ["guest_name", "phone"]


def test_a_must_have_blocks_the_step_it_belongs_to_and_is_asked_in_order():
    state = ConversationState()
    config = _config(budget="required", dates="required")
    decision = collect_gate.evaluate(state, config, "recommend")
    assert decision.block and decision.ask_for == ["dates", "budget"]
    assert "stay dates" in decision.text and "budget" in decision.text
    assert "Do not mention a price" in decision.text


def test_details_the_tool_was_just_handed_count_as_known():
    state, config = ConversationState(), _config(budget="required", guests="required")
    assert collect_gate.evaluate(state, config, "recommend", frozenset({"budget", "guests"})).block is False


def test_known_slots_satisfy_the_gate_and_nights_count_as_dates():
    state = ConversationState()
    state.slots.update({"guest_name": "Asha", "phone": "+919999999999", "nights": 3, "num_guests": 2})
    config = _config(dates="required", guests="required")
    assert collect_gate.evaluate(state, config, "funnel").block is False


def test_a_detail_is_asked_at_most_twice_then_waived_and_remembered():
    state, config = ConversationState(), _config(budget="required")

    state.turn_index = 1
    first = collect_gate.evaluate(state, config, "recommend")
    assert first.block and state.collect_nudges["budget"] == 1

    # A retry inside the same guest turn repeats the instruction, it is not a new ask.
    assert collect_gate.evaluate(state, config, "recommend").block
    assert state.collect_nudges["budget"] == 1

    state.turn_index = 2
    second = collect_gate.evaluate(state, config, "recommend")
    assert second.block and state.collect_nudges["budget"] == 2

    # Still the second ask's own turn: still asks, does not waive early.
    assert collect_gate.evaluate(state, config, "recommend").block

    state.turn_index = 3
    third = collect_gate.evaluate(state, config, "recommend")
    assert third.block is False and third.waived == ["budget"]
    assert state.waived_fields == ["budget"]
    assert state.collect_nudges["budget"] == 2  # never nudged a third time

    # Waived stays waived on later steps too.
    assert collect_gate.evaluate(state, config, "funnel", frozenset({"guest_name", "phone"})).block is False


def test_each_detail_has_its_own_two_asks():
    state, config = ConversationState(), _config(budget="required", guests="required")
    state.turn_index = 1
    collect_gate.evaluate(state, config, "recommend")
    state.turn_index = 2
    state.slots["budget"] = 5000  # answered after the first ask
    decision = collect_gate.evaluate(state, config, "recommend")
    assert decision.ask_for == ["guests"] and state.collect_nudges == {"budget": 1, "guests": 2}


def test_a_waived_detail_is_surfaced_to_the_model_so_it_stops_asking():
    from app.voice.state_prompt_sync import build_state_block_content

    state = ConversationState()
    assert build_state_block_content(state) == ""  # nothing to say yet
    state.waived_fields.append("budget")
    block = build_state_block_content(state)
    assert "their budget" in block and "Do not ask for it again" in block
