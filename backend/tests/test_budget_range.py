"""Price RANGE ("10-15k for the whole stay") and "around X" budgets, on top of
the single-ceiling budget semantics in test_recommend_properties_budget.py.

Origin: a tested call where the guest gave a price range / "around 4k" and the
agent could not sort properties into it -- recommend_properties only had one
ceiling figure, so the model had to guess which end of a range to pass.

What these tests pin down:
- a range keeps BOTH ends (nothing below the floor is a match);
- "around X" is a +/-15% band, not a hard ceiling;
- a plain "under X" budget behaves exactly as before (no floor, ever);
- budget_min is only stored while the budget really is a range.
"""

import logging
import uuid

import pytest

from app.models.property import Property
from app.prompts import system_prompt
from app.schemas.tool import RecommendPropertiesArgs
from app.services.property.budget import canonical_budget_band, normalize_budget
from app.services.property.pitch_formatter import render_recommendation_text
from app.services.property.retrieval import orchestrator
from app.voice.conversation_state import ConversationState
from app.voice.state_prompt_sync import _format_slots
from app.voice.tools import build_voice_tools


class _Params:
    def __init__(self, guest_turns: list[str] | None = None):
        self.result = None
        self.envelope = None
        self.context = _Context(guest_turns or [])

    async def result_callback(self, result):
        self.envelope = result
        self.result = result.get("result", result.get("message")) if isinstance(result, dict) else result


class _Context:
    def __init__(self, turns):
        self._messages = [{"role": "user", "content": t} for t in turns]

    def get_messages(self):
        return self._messages


async def _property(db_session, test_user, **overrides):
    defaults = dict(
        user_id=test_user.id,
        name="Test Villa",
        city="Udaipur",
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


def _tools(state, host_user_id) -> dict:
    return {
        t.__name__: t
        for t in build_voice_tools(
            call_session_id=None, property_id=None, host_user_id=host_user_id, conversation_state=state
        )
    }


def _names(result):
    return [c.spoken_name for c in result.options]


def _near(result):
    return [c.spoken_name for c in result.near_budget]


# --- canonical_budget_band (pure) --------------------------------------------------


def test_single_figure_is_not_a_range():
    assert canonical_budget_band(10000) == (10000.0, None, False)
    assert canonical_budget_band(10000, None, True) == (10000.0, None, True)


def test_a_real_range_keeps_both_ends():
    assert canonical_budget_band(15000, 10000) == (15000.0, 10000.0, False)


def test_a_range_given_the_wrong_way_round_is_swapped():
    assert canonical_budget_band(10000, 15000) == (15000.0, 10000.0, False)


def test_equal_ends_are_just_one_budget():
    assert canonical_budget_band(10000, 10000) == (10000.0, None, False)


def test_around_is_dropped_for_an_explicit_range():
    assert canonical_budget_band(15000, 10000, True) == (15000.0, 10000.0, False)


def test_a_min_with_no_max_is_no_budget():
    assert canonical_budget_band(None, 10000) == (None, None, False)
    assert canonical_budget_band(0, 10000) == (None, None, False)


def test_non_positive_min_is_ignored():
    assert canonical_budget_band(15000, 0) == (15000.0, None, False)
    assert canonical_budget_band(15000, -5) == (15000.0, None, False)


# --- BudgetConstraint: positions (pure) --------------------------------------------


def test_plain_budget_has_no_floor_so_cheap_always_fits():
    c = normalize_budget(10000, "per_night", 2)
    assert c.floor is None and not c.is_range
    assert c.position(2500) == "within" and c.fits(2500)
    assert not c.just_below_range(2500)
    assert c.position(10001) == "above"


def test_per_night_range_positions():
    c = normalize_budget(15000, "per_night", 2, min_amount=10000)
    assert [c.position(p) for p in (9999, 10000, 12000, 15000, 15001)] == [
        "below",
        "within",
        "within",
        "within",
        "above",
    ]
    assert c.max_total_rate == 30000 and c.min_nightly_rate == 10000


def test_total_stay_range_compares_the_stay_total_directly():
    c = normalize_budget(15000, "total_stay", 3, min_amount=10000)
    assert c.max_nightly_rate == 5000 and c.min_nightly_rate == pytest.approx(10000 / 3)
    assert c.fits(4000, 12000)
    assert c.position(2500, 7500) == "below"
    assert c.position(5500, 16500) == "above"
    # exact edges on the stay total
    assert c.fits(3333, 10000) and c.fits(5000, 15000)


def test_one_night_with_no_basis_needs_no_question_for_a_range():
    c = normalize_budget(15000, None, 1, min_amount=10000)
    assert c.clarification is None and c.fits(12000) and c.position(9000) == "below"


def test_range_with_unknown_basis_asks_like_a_single_budget():
    assert normalize_budget(15000, None, None, min_amount=10000).clarification == "stay_length"
    assert normalize_budget(15000, None, 3, min_amount=10000).clarification == "budget_basis"
    assumed = normalize_budget(15000, None, 3, assume_per_night=True, min_amount=10000)
    assert assumed.basis == "per_night" and assumed.min_nightly_rate == 10000 and assumed.basis_assumed


def test_around_is_a_15_percent_band():
    c = normalize_budget(4000, "per_night", 2, approximate=True)
    assert (c.floor, c.ceiling) == (3400.0, 4600.0)
    assert [c.position(p) for p in (2500, 3399, 3400, 4000, 4600, 4601)] == [
        "below",
        "below",
        "within",
        "within",
        "within",
        "above",
    ]


def test_around_on_a_total_budget_scales_to_nightly():
    c = normalize_budget(12000, "total_stay", 3, approximate=True)
    assert c.max_total_rate == 13800 and c.max_nightly_rate == pytest.approx(4600)
    assert c.min_nightly_rate == pytest.approx(3400)
    assert c.fits(4000, 12000) and not c.fits(2000, 6000)


def test_stretch_applies_on_both_sides_but_only_for_a_floor():
    c = normalize_budget(15000, "per_night", 2, min_amount=10000)
    assert c.within_stretch(16000) and not c.within_stretch(17251)
    assert c.just_below_range(8500) and c.just_below_range(9500) and not c.just_below_range(8499)
    assert not c.just_below_range(12000)  # inside the range
    plain = normalize_budget(15000, "per_night", 2)
    assert not plain.just_below_range(8500)


def test_a_price_under_the_floor_is_never_over_budget_or_an_upsell():
    c = normalize_budget(15000, "per_night", 2, min_amount=10000)
    assert not c.over_but_within(1.5, 5000)
    assert c.over_but_within(1.5, 20000) and not c.over_but_within(1.5, 23000)
    assert c.over_budget_by(5000) == 0 and c.under_budget_by(8000) == 2000


def test_an_unpriced_property_never_fits_a_range():
    c = normalize_budget(15000, "per_night", 2, min_amount=10000)
    assert c.position(0) == "unknown" and not c.fits(0) and not c.just_below_range(0)


# --- Eligibility against real properties -------------------------------------------


async def _portfolio(db_session, test_user, prices: dict[str, int]):
    for name, price in prices.items():
        await _property(db_session, test_user, name=name, base_price=price)


async def test_per_night_range_only_matches_inside_it(db_session, test_user):
    await _portfolio(
        db_session,
        test_user,
        {"Cheap": 3000, "Almost": 9000, "Mid": 12000, "Top": 14500, "Pricey": 16000, "Palace": 30000},
    )
    args = RecommendPropertiesArgs(budget_amount=15000, budget_min=10000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert _names(result) == ["Mid", "Top"]
    assert "Cheap" not in render_recommendation_text(result) and "Palace" not in render_recommendation_text(result)
    assert "Budget applied: ₹10,000 to ₹15,000 per night (₹20,000 to ₹30,000 for 2 nights)." in (
        render_recommendation_text(result)
    )


async def test_whole_stay_range_uses_the_stay_total(db_session, test_user):
    """"10-15k for the whole stay", 2 nights: a stay total of 12,000 and 14,000
    fits; 8,000 (too cheap) and 16,000 (too dear) do not."""
    await _portfolio(db_session, test_user, {"Budget": 4000, "Fits A": 6000, "Fits B": 7000, "Dear": 8000})
    args = RecommendPropertiesArgs(budget_amount=15000, budget_min=10000, budget_basis="total_stay", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert _names(result) == ["Fits A", "Fits B"]
    assert "₹10,000 to ₹15,000 total for 2 nights (about ₹5,000 to ₹7,500 a night)" in render_recommendation_text(result)


async def test_nothing_in_range_offers_close_alternatives_on_both_sides_labelled(db_session, test_user):
    await _portfolio(db_session, test_user, {"Just Under": 9000, "Just Over": 16000, "Far Under": 3000})
    args = RecommendPropertiesArgs(budget_amount=15000, budget_min=10000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert _names(result) == []
    assert sorted(_near(result)) == ["Just Over", "Just Under"]
    text = render_recommendation_text(result)
    assert "Nothing is within the guest's budget" in text
    assert "Just Under" in text and "Slightly BELOW the guest's budget range" in text and "₹1,000 a night under" in text
    assert "Just Over" in text and "Slightly ABOVE the guest's budget" in text and "₹1,000 a night over" in text
    assert "Far Under" not in text


async def test_range_alternatives_never_displace_a_real_match(db_session, test_user):
    await _portfolio(db_session, test_user, {"In": 12000, "Just Under": 9000, "Just Over": 16000})
    args = RecommendPropertiesArgs(budget_amount=15000, budget_min=10000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert _names(result) == ["In"]
    assert len(_near(result)) == 2  # only fills the two otherwise-empty slots


async def test_far_over_property_is_not_dragged_in_as_an_upsell_just_because_a_range_was_given(db_session, test_user):
    await _property(db_session, test_user, name="Under", base_price=5000, is_premium=True, amenity_tags=["pool"])
    args = RecommendPropertiesArgs(
        budget_amount=15000, budget_min=10000, budget_basis="per_night", nights=2, required_amenities=["pool"]
    )
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert result.upsell is None and _names(result) == []


async def test_around_budget_matches_the_band_not_just_a_ceiling(db_session, test_user):
    await _portfolio(
        db_session,
        test_user,
        {"Too Cheap": 2500, "Low": 3500, "Exact": 4000, "High": 4500, "Stretch": 4800, "Far": 6000},
    )
    args = RecommendPropertiesArgs(budget_amount=4000, budget_approximate=True, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert _names(result) == ["Low", "Exact", "High"]
    text = render_recommendation_text(result)
    assert "around ₹4,000 per night" in text and "roughly ₹3,400 to ₹4,600" in text
    assert "Too Cheap" not in text and "Far" not in text


async def test_around_with_nothing_inside_falls_back_to_the_close_ones(db_session, test_user):
    await _portfolio(db_session, test_user, {"Edge": 4800, "Far": 9000})
    args = RecommendPropertiesArgs(budget_amount=4000, budget_approximate=True, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert _names(result) == [] and _near(result) == ["Edge"]


async def test_under_x_is_exactly_as_before_cheap_properties_still_match(db_session, test_user):
    await _portfolio(db_session, test_user, {"Cheap": 2500, "Mid": 9000, "Over": 11000})
    args = RecommendPropertiesArgs(budget_amount=10000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert _names(result) == ["Cheap", "Mid"]
    assert _near(result) == ["Over"]
    assert "BELOW" not in render_recommendation_text(result)


async def test_range_clarification_names_both_figures(db_session, test_user):
    await _property(db_session, test_user, name="Lake Haveli", base_price=12000)
    args = RecommendPropertiesArgs(budget_amount=15000, budget_min=10000, nights=3)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert "₹10,000 to ₹15,000 per night, or ₹10,000 to ₹15,000 for the entire stay" in render_recommendation_text(result)


# --- Slots: budget_min exists only while the budget is a range -----------------------


def test_resolve_band_keeps_a_stored_range_when_the_call_omits_the_budget():
    state = ConversationState()
    state.store_budget_band(15000, 10000, False)
    assert state.resolve_budget_band(None, None, False) == (15000.0, 10000.0, False)


def test_resolve_band_keeps_the_range_when_the_same_figure_is_restated():
    state = ConversationState()
    state.store_budget_band(15000, 10000, False)
    assert state.resolve_budget_band(15000, None, False) == (15000.0, 10000.0, False)


def test_resolve_band_drops_the_range_for_a_different_figure():
    state = ConversationState()
    state.store_budget_band(15000, 10000, False)
    assert state.resolve_budget_band(8000, None, False) == (8000.0, None, False)


def test_resolve_band_explicit_zero_min_drops_the_lower_bound():
    state = ConversationState()
    state.store_budget_band(15000, 10000, False)
    assert state.resolve_budget_band(15000, 0, False) == (15000.0, None, False)


def test_resolve_band_keeps_around_and_a_new_range_replaces_it():
    state = ConversationState()
    state.store_budget_band(4000, None, True)
    assert state.resolve_budget_band(None, None, False) == (4000.0, None, True)
    assert state.resolve_budget_band(15000, 10000, False) == (15000.0, 10000.0, False)


def test_store_band_removes_stale_range_slots():
    state = ConversationState()
    state.store_budget_band(15000, 10000, False)
    state.store_budget_band(8000, None, False)
    assert state.slots["budget"] == 8000 and "budget_min" not in state.slots
    state.store_budget_band(4000, None, True)
    assert state.slots["budget_approximate"] is True and "budget_min" not in state.slots
    state.store_budget_band(6000, None, False)
    assert "budget_approximate" not in state.slots


def test_no_budget_stores_nothing():
    state = ConversationState()
    state.store_budget_band(None, 10000, False)
    assert state.slots == {}


def test_state_prompt_shows_both_ends_of_a_range_and_around():
    state = ConversationState()
    state.store_budget_band(15000, 10000, False)
    state.slots["budget_basis"] = "total_stay"
    assert "₹10,000 to ₹15,000 total for the stay" in _format_slots(state)
    around = ConversationState()
    around.store_budget_band(4000, None, True)
    assert "around ₹4,000" in _format_slots(around)
    plain = ConversationState()
    plain.store_budget_band(4000, None, False)
    assert "~₹4,000" in _format_slots(plain)


# --- The tools the model actually calls ----------------------------------------------


async def test_recommend_properties_range_end_to_end(db_session, test_user):
    await _portfolio(db_session, test_user, {"Cheap": 3000, "Mid": 12000, "Pricey": 20000})
    state = ConversationState()
    params = _Params(guest_turns=["my budget is 10 to 15 thousand a night"])
    await _tools(state, test_user.id)["recommend_properties"](
        params, budget_amount=15000, budget_min=10000, budget_basis="per_night", nights=2
    )
    assert params.envelope["status"] == "ok"
    assert "Mid" in params.result and "Cheap" not in params.result and "Pricey" not in params.result
    assert state.slots["budget"] == 15000 and state.slots["budget_min"] == 10000


async def test_a_refinement_that_omits_the_budget_keeps_the_range(db_session, test_user):
    await _portfolio(db_session, test_user, {"Cheap": 3000, "Mid": 12000, "Pool": 13000})
    state = ConversationState()
    tools = _tools(state, test_user.id)
    await tools["recommend_properties"](
        _Params(), budget_amount=15000, budget_min=10000, budget_basis="per_night", nights=2
    )
    params = _Params()
    await tools["recommend_properties"](params, preferred_location="Udaipur")
    assert "Cheap" not in params.result and "Mid" in params.result
    assert "₹10,000 to ₹15,000 per night" in params.result


async def test_a_new_plain_budget_drops_the_old_range(db_session, test_user):
    await _portfolio(db_session, test_user, {"Cheap": 3000, "Mid": 12000})
    state = ConversationState()
    tools = _tools(state, test_user.id)
    await tools["recommend_properties"](
        _Params(), budget_amount=15000, budget_min=10000, budget_basis="per_night", nights=2
    )
    params = _Params()
    await tools["recommend_properties"](params, budget_amount=5000, budget_basis="per_night", nights=2)
    assert "Cheap" in params.result and "Mid" not in params.result
    assert "budget_min" not in state.slots and state.slots["budget"] == 5000


async def test_something_cheaper_ignores_the_old_floor(db_session, test_user):
    await _portfolio(db_session, test_user, {"Cheap": 3000, "Mid": 12000})
    state = ConversationState()
    tools = _tools(state, test_user.id)
    await tools["recommend_properties"](
        _Params(), budget_amount=15000, budget_min=10000, budget_basis="per_night", nights=2
    )
    params = _Params()
    await tools["recommend_properties"](params, cheaper_than_shown=True)
    assert "Cheap" in params.result  # a 10k floor must not hide the cheaper options asked for
    assert state.slots["budget"] == 15000 and state.slots["budget_min"] == 10000  # derived ceiling never persisted


async def test_around_end_to_end_and_it_is_remembered(db_session, test_user):
    await _portfolio(db_session, test_user, {"Too Cheap": 2000, "Fit": 4200, "Far": 9000})
    state = ConversationState()
    tools = _tools(state, test_user.id)
    params = _Params(guest_turns=["around 4k per night"])
    await tools["recommend_properties"](
        params, budget_amount=4000, budget_approximate=True, budget_basis="per_night", nights=2
    )
    assert "Fit" in params.result and "Too Cheap" not in params.result and "Far" not in params.result
    assert state.slots["budget_approximate"] is True
    again = _Params()
    await tools["recommend_properties"](again, preferred_location="Udaipur")
    assert "Fit" in again.result and "Too Cheap" not in again.result


async def test_legacy_argument_names_for_the_ends_of_a_range_still_work(db_session, test_user):
    await _portfolio(db_session, test_user, {"Cheap": 3000, "Mid": 12000})
    state = ConversationState()
    params = _Params()
    await _tools(state, test_user.id)["recommend_properties"](
        params, budget_max=15000, min_budget=10000, budget_basis="per_night", nights=2
    )
    assert "Mid" in params.result and "Cheap" not in params.result and state.slots["budget_min"] == 10000


async def test_swapped_range_from_the_model_still_searches_the_right_band(db_session, test_user):
    await _portfolio(db_session, test_user, {"Cheap": 3000, "Mid": 12000})
    state = ConversationState()
    params = _Params()
    await _tools(state, test_user.id)["recommend_properties"](
        params, budget_amount=10000, budget_min=15000, budget_basis="per_night", nights=2
    )
    assert "Mid" in params.result and "Cheap" not in params.result
    assert state.slots["budget"] == 15000 and state.slots["budget_min"] == 10000


async def test_update_lead_range_is_what_recommend_backfills(db_session, test_user):
    await _portfolio(db_session, test_user, {"Cheap": 3000, "Mid": 6000, "Dear": 9000})
    state = ConversationState()
    tools = _tools(state, test_user.id)
    await tools["update_lead"](
        _Params(), budget_amount=15000, budget_min=10000, budget_basis="total_stay", nights=2
    )
    assert state.slots["budget_min"] == 10000
    params = _Params()
    await tools["recommend_properties"](params)
    assert "Mid" in params.result and "Cheap" not in params.result and "Dear" not in params.result
    assert "₹10,000 to ₹15,000 total for 2 nights" in params.result


async def test_update_lead_plain_budget_clears_a_stale_range(db_session, test_user):
    state = ConversationState()
    tools = _tools(state, test_user.id)
    await tools["update_lead"](_Params(), budget_amount=15000, budget_min=10000, budget_basis="per_night", nights=2)
    await tools["update_lead"](_Params(), budget_amount=7000, budget_basis="per_night")
    assert state.slots["budget"] == 7000 and "budget_min" not in state.slots


async def test_update_lead_without_a_budget_leaves_the_range_alone(db_session, test_user):
    state = ConversationState()
    tools = _tools(state, test_user.id)
    await tools["update_lead"](_Params(), budget_amount=15000, budget_min=10000, budget_basis="per_night", nights=2)
    await tools["update_lead"](_Params(), num_guests=3)
    assert state.slots["budget"] == 15000 and state.slots["budget_min"] == 10000


async def test_range_survives_the_clarification_questions(db_session, test_user):
    """"10 to 15k" -> stay length asked -> per night or total asked -> answered:
    the range must still be there on the call that finally searches."""
    await _portfolio(db_session, test_user, {"Cheap": 3000, "Mid": 6000, "Dear": 9000})
    state = ConversationState()
    tools = _tools(state, test_user.id)

    first = _Params(guest_turns=["my budget is 10 to 15 thousand"])
    await tools["recommend_properties"](first, budget_amount=15000, budget_min=10000)
    assert first.envelope["status"] == "needs_clarification" and "how many nights" in first.result
    assert "Mid" not in first.result  # nothing is named before the question is answered

    second = _Params(guest_turns=["two nights"])
    await tools["recommend_properties"](second, nights=2)
    assert second.envelope["status"] == "needs_clarification"
    assert "₹10,000 to ₹15,000 per night, or ₹10,000 to ₹15,000 for the entire stay" in second.result

    third = _Params(guest_turns=["for the whole stay"])
    await tools["recommend_properties"](third, budget_basis="total_stay", nights=2)
    assert third.envelope["status"] == "ok"
    assert "Mid" in third.result and "Cheap" not in third.result and "Dear" not in third.result


async def test_basis_is_read_from_the_guests_words_for_a_range(db_session, test_user):
    await _portfolio(db_session, test_user, {"Cheap": 3000, "Mid": 6000})
    state = ConversationState()
    params = _Params(guest_turns=["10 to 15k for the whole stay, 2 nights"])
    await _tools(state, test_user.id)["recommend_properties"](
        params, budget_amount=15000, budget_min=10000, nights=2
    )
    assert params.envelope["status"] == "ok"
    assert "₹10,000 to ₹15,000 total for 2 nights" in params.result
    assert "Mid" in params.result and "Cheap" not in params.result


async def test_a_plain_budget_call_never_touches_range_state(db_session, test_user):
    """Regression guard for the ordinary "under X" flow."""
    await _portfolio(db_session, test_user, {"Cheap": 3000, "Mid": 6000})
    state = ConversationState()
    params = _Params(guest_turns=["under 7k a night"])
    await _tools(state, test_user.id)["recommend_properties"](
        params, budget_amount=7000, budget_basis="per_night", nights=2
    )
    assert "Cheap" in params.result and "Mid" in params.result
    assert state.slots["budget"] == 7000
    assert "budget_min" not in state.slots and "budget_approximate" not in state.slots


# --- The prompt tells the model how to pass them -------------------------------------


def test_the_prompt_teaches_ranges_and_around():
    prompt = system_prompt.GOLDEN_RULES if hasattr(system_prompt, "GOLDEN_RULES") else ""
    assert "budget_min" in prompt and "budget_approximate" in prompt
    assert "never set budget_min for a single figure" in prompt


# --- Decision log -----------------------------------------------------------------


def test_decision_log_line_formats_with_and_without_a_range(caplog):
    """A %-format/argument mismatch in logger.info is swallowed by logging --
    it would silently lose the one line that explains a live call's result."""
    with caplog.at_level(logging.INFO, logger=orchestrator.__name__):
        ranged = RecommendPropertiesArgs(budget_amount=15000, budget_min=10000, budget_basis="per_night", nights=2)
        orchestrator._log_decision(ranged, ranged.budget_constraint(), None, candidate_count=3)
        around = RecommendPropertiesArgs(budget_amount=4000, budget_approximate=True, budget_basis="per_night")
        orchestrator._log_decision(around, around.budget_constraint(), None, candidate_count=1)
        orchestrator._log_decision(RecommendPropertiesArgs(), None, None, candidate_count=0)
    # dict.fromkeys: de-duplicate (caplog can see a record via more than one handler), keep order.
    lines = list(
        dict.fromkeys(r.getMessage() for r in caplog.records if "recommend_properties_decision" in r.getMessage())
    )
    assert len(lines) == 3
    assert "budget_amount=15000.0 budget_min=10000.0 budget_approximate=False budget_basis=per_night" in lines[0]
    assert "budget_amount=4000.0 budget_min=None budget_approximate=True" in lines[1]
    assert "budget_amount=None budget_min=None budget_approximate=None" in lines[2]
