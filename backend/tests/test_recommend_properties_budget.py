"""Budget-based recommendations: explicit budget semantics (app/services/
property/budget.py), applicable-stay pricing as the source of truth for
eligibility (pricing_engine.evaluate_stay_prices), the host's automatic
length-of-stay offers, ambiguity handling, and failure safety.

Origin (confirmed live 2026-09-25): a guest said "₹7000 per night", the LLM
called recommend_properties(budget=7000, nights=1, ...), the call died with
"unexpected keyword argument 'nights'" (pipecat delivered NO tool result),
and the agent went on as if nothing fit the budget.
"""

import uuid
from datetime import date, timedelta

from pipecat.adapters.schemas.direct_function import DirectFunctionWrapper
from pipecat.frames.frames import (
    FunctionCallFromLLM,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.tests.utils import run_test

from app.integrations import redis_client
from app.integrations.searchapi_client import nightly_rate_cache_key
from app.models.pricing_rule import PricingRule
from app.models.property import Property
from app.schemas.tool import RecommendPropertiesArgs
from app.services import tool_handlers
from app.services.property.budget import infer_budget_basis, normalize_budget
from app.services.property.card import PropertyCard
from app.services.property.pitch_formatter import (
    RecommendationResult,
    recommendation_failed_result,
    render_recommendation_text,
)
from app.services.property.retrieval import orchestrator
from app.voice.conversation_state import ConversationState
from app.voice.property_recommendation_guard import PropertyRecommendationGuardProcessor
from app.voice.tools import build_voice_tools


class _FakeFunctionCallParams:
    def __init__(self, guest_turns: list[str] | None = None):
        self.result = None
        self.envelope = None
        self.context = _FakeContext(guest_turns or [])

    async def result_callback(self, result):
        self.envelope = result
        self.result = result.get("result", result.get("message")) if isinstance(result, dict) else result


class _FakeContext:
    def __init__(self, guest_turns: list[str]):
        self._messages = [{"role": "user", "content": t} for t in guest_turns]

    def get_messages(self):
        return self._messages


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value


async def _cache_nightly_rates(monkeypatch, listing_id: str, check_in: date, rates: list[float]) -> None:
    monkeypatch.setattr(redis_client, "_client", _FakeRedis())
    monkeypatch.setattr(redis_client, "_client_initialized", True)
    for i, rate in enumerate(rates):
        await redis_client.cache_set_json(nightly_rate_cache_key(listing_id, check_in + timedelta(days=i)), rate, 3600)


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


async def _stay_offer(db_session, property_, percent: float, min_nights: int) -> None:
    db_session.add(
        PricingRule(
            property_id=property_.id,
            rule_type="length_of_stay",
            condition={"min_nights": min_nights},
            discount_percent=percent,
            active=True,
        )
    )
    await db_session.commit()


def _tools(state: ConversationState, host_user_id, guard=None) -> dict:
    return {
        t.__name__: t
        for t in build_voice_tools(
            call_session_id=None,
            property_id=None,
            host_user_id=host_user_id,
            conversation_state=state,
            property_recommendation_guard=guard,
        )
    }


def _names(result: RecommendationResult) -> list[str]:
    return [card.spoken_name for card in result.options]


def _exact_stay(nights: int = 2) -> tuple[date, date]:
    check_in = date.today() + timedelta(days=2)
    return check_in, check_in + timedelta(days=nights)


def _call_started() -> FunctionCallsStartedFrame:
    return FunctionCallsStartedFrame(
        function_calls=[
            FunctionCallFromLLM(function_name="recommend_properties", tool_call_id="tc_1", arguments={}, context=None)
        ]
    )


def _spoken(frames) -> str:
    return "".join(f.text for f in frames if isinstance(f, LLMTextFrame))


# --- Normalization (pure) --------------------------------------------------


def test_per_night_budget_normalizes_to_nightly_and_total_ceilings():
    budget = normalize_budget(7000, "per_night", 2)
    assert (budget.max_nightly_rate, budget.max_total_rate) == (7000, 14000)


def test_total_stay_budget_normalizes_to_divided_nightly_ceiling():
    budget = normalize_budget(7000, "total_stay", 2)
    assert (budget.max_nightly_rate, budget.max_total_rate) == (3500, 7000)


def test_per_night_budget_never_becomes_half_for_a_two_night_stay():
    budget = normalize_budget(7000, "per_night", 2)
    assert budget.max_nightly_rate == 7000
    assert budget.fits(6900, 13800)


def test_total_stay_without_stay_length_asks_for_nights():
    assert normalize_budget(14000, "total_stay", None).clarification == "stay_length"


def test_zero_or_negative_nights_are_treated_as_unknown():
    assert normalize_budget(14000, "total_stay", 0).clarification == "stay_length"
    assert normalize_budget(7000, "per_night", -1).max_total_rate is None


def test_non_inr_budget_asks_rather_than_converting():
    assert normalize_budget(100, "per_night", 2, currency="usd").clarification == "currency"


def test_no_amount_means_no_budget_filter():
    assert normalize_budget(None, "per_night", 2) is None
    assert normalize_budget(0, "per_night", 2) is None


def test_ambiguous_basis_one_night_needs_no_clarification():
    budget = normalize_budget(7000, None, 1)
    assert budget.clarification is None
    assert budget.max_nightly_rate == 7000 == budget.max_total_rate


def test_ambiguous_basis_multi_night_asks_the_basis():
    budget = normalize_budget(7000, None, 2)
    assert budget.basis == "unspecified"
    assert budget.clarification == "budget_basis"
    assert budget.max_nightly_rate is None


def test_ambiguous_basis_unknown_nights_asks_stay_length_not_basis():
    assert normalize_budget(7000, None, None).clarification == "stay_length"


def test_after_one_clarification_per_night_is_assumed_not_asked_again():
    budget = normalize_budget(7000, None, 2, assume_per_night=True)
    assert budget.clarification is None
    assert budget.basis == "per_night" and budget.basis_assumed
    assert budget.max_nightly_rate == 7000


def test_nights_known_never_implies_total_stay():
    assert normalize_budget(7000, "unspecified", 2).basis == "unspecified"


def test_near_budget_stretch_is_separate_from_fitting():
    budget = normalize_budget(7000, "per_night", 2)
    assert not budget.fits(7500, 15000)
    assert budget.within_stretch(7500, 15000)
    assert not budget.within_stretch(9000, 18000)


# --- Guest phrase -> basis ---------------------------------------------------------


def test_per_night_phrasings():
    for text in ("₹7,000 per night", "7k a night", "7000 nightly", "around 7k per night"):
        assert infer_budget_basis(text) == "per_night", text


def test_total_stay_phrasings():
    for text in (
        "7000 total for two nights",
        "₹14,000 total for two nights",
        "my total budget is 7000",
        "7000 for the whole stay",
    ):
        assert infer_budget_basis(text) == "total_stay", text


def test_plain_budget_or_under_x_has_no_basis():
    assert infer_budget_basis("my budget is 7000") is None
    assert infer_budget_basis("under 7k") is None


def test_guest_count_phrasing_is_not_a_total_budget():
    assert infer_budget_basis("we are total 4 people") is None


def test_both_readings_in_one_utterance_is_no_signal():
    assert infer_budget_basis("7k a night so 14k total") is None


# --- Applicable-price eligibility (real DB) ---------------------------------------


async def test_rate_6500_fits_7000_per_night(db_session, test_user):
    await _property(db_session, test_user, name="Lake Haveli", base_price=6500)
    args = RecommendPropertiesArgs(budget_amount=7000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert _names(result) == ["Lake Haveli"]


async def test_rate_7500_excluded_from_7000_per_night_but_labelled_near_budget(db_session, test_user):
    await _property(db_session, test_user, name="Palace Suite", base_price=7500)
    args = RecommendPropertiesArgs(budget_amount=7000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert _names(result) == []
    assert [c.spoken_name for c in result.near_budget] == ["Palace Suite"]
    text = render_recommendation_text(result)
    assert "ABOVE the guest's budget" in text and "₹500 a night over" in text


async def test_total_13000_fits_14000_total(db_session, test_user):
    await _property(db_session, test_user, name="Lake Haveli", base_price=6500)
    args = RecommendPropertiesArgs(budget_amount=14000, budget_basis="total_stay", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert _names(result) == ["Lake Haveli"]
    assert "₹13,000 for your 2 nights" in render_recommendation_text(result)


async def test_total_15000_excluded_from_14000_total(db_session, test_user):
    await _property(db_session, test_user, name="Palace Suite", base_price=7500)
    args = RecommendPropertiesArgs(budget_amount=14000, budget_basis="total_stay", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert _names(result) == []
    assert "₹15,000 for 2 nights, ₹1,000 over their total budget" in render_recommendation_text(result)


async def test_base_price_over_budget_but_stay_offer_brings_it_within(db_session, test_user):
    """₹7,500/night standard, but the host's 10%-off-for-2+-nights offer
    makes the stay ₹13,500 (₹6,750/night) -- eligible, and pitched as an
    offer."""
    villa = await _property(db_session, test_user, name="Offer Villa", base_price=7500)
    await _stay_offer(db_session, villa, percent=10, min_nights=2)
    args = RecommendPropertiesArgs(budget_amount=7000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert _names(result) == ["Offer Villa"]
    text = render_recommendation_text(result)
    assert "2+ night offer (10% off)" in text
    assert "come to ₹13,500 instead of ₹15,000" in text
    assert "OFFER: Offer Villa" in text and "within their budget" in text


async def test_stay_offer_does_not_apply_below_its_minimum_nights(db_session, test_user):
    villa = await _property(db_session, test_user, name="Offer Villa", base_price=7500)
    await _stay_offer(db_session, villa, percent=10, min_nights=3)
    args = RecommendPropertiesArgs(budget_amount=7000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert _names(result) == []


async def test_base_price_under_budget_but_live_rate_over_is_excluded(db_session, test_user, monkeypatch):
    check_in, check_out = _exact_stay(2)
    await _property(
        db_session,
        test_user,
        name="Airbnb Loft",
        base_price=6000,
        exact_airbnb_pricing=True,
        airbnb_listing_id="loft-1",
    )
    await _cache_nightly_rates(monkeypatch, "loft-1", check_in, [8200, 8200])
    args = RecommendPropertiesArgs(budget_amount=7000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(
        db_session, args, test_user.id, stay_check_in=check_in, stay_check_out=check_out
    )
    assert _names(result) == [] and result.near_budget == []


async def test_base_price_over_budget_but_live_rate_under_is_eligible(db_session, test_user, monkeypatch):
    check_in, check_out = _exact_stay(2)
    await _property(
        db_session,
        test_user,
        name="Airbnb Loft",
        base_price=9000,
        exact_airbnb_pricing=True,
        airbnb_listing_id="loft-2",
    )
    await _cache_nightly_rates(monkeypatch, "loft-2", check_in, [6400, 6600])
    args = RecommendPropertiesArgs(budget_amount=7000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(
        db_session, args, test_user.id, stay_check_in=check_in, stay_check_out=check_out
    )
    assert _names(result) == ["Airbnb Loft"]
    assert "₹13,000 for your 2 nights" in render_recommendation_text(result)


async def test_uncached_airbnb_rate_is_an_estimate_never_a_live_fetch(db_session, test_user, monkeypatch):
    from app.services import pricing_engine

    async def _no_live_fetch(*args, **kwargs):
        raise AssertionError("recommendations must never trigger a live SearchApi fetch")

    monkeypatch.setattr(pricing_engine, "fetch_listing_total_price", _no_live_fetch)
    check_in, check_out = _exact_stay(2)
    await _property(
        db_session, test_user, name="Airbnb Loft", base_price=6000, exact_airbnb_pricing=True, airbnb_listing_id="x"
    )
    args = RecommendPropertiesArgs(budget_amount=7000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(
        db_session, args, test_user.id, stay_check_in=check_in, stay_check_out=check_out
    )
    assert _names(result) == ["Airbnb Loft"]
    assert "an estimate until the exact dates are priced" in render_recommendation_text(result)


async def test_candidate_stage_never_drops_a_property_an_offer_makes_eligible(db_session, test_user):
    """Stage 1 has no price clause: a property that looks over budget on
    base_price must still be priced and, thanks to its offer, recommended."""
    await _property(db_session, test_user, name="Budget Room", base_price=5000)
    await _property(db_session, test_user, name="Out Of Budget", base_price=9500)
    villa = await _property(db_session, test_user, name="Offer Villa", base_price=8000)
    await _stay_offer(db_session, villa, percent=20, min_nights=3)
    args = RecommendPropertiesArgs(budget_amount=7000, budget_basis="per_night", nights=3)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert sorted(_names(result)) == ["Budget Room", "Offer Villa"]


async def test_unpriced_property_is_never_offered_as_within_budget(db_session, test_user):
    await _property(db_session, test_user, name="Unpriced Airbnb", base_price=0, exact_airbnb_pricing=True)
    args = RecommendPropertiesArgs(budget_amount=7000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert _names(result) == [] and result.near_budget == []


async def test_near_budget_alternatives_never_displace_real_matches(db_session, test_user):
    for i, price in enumerate([5000, 6000, 6800, 7500]):
        await _property(db_session, test_user, name=f"Villa {i}", base_price=price)
    args = RecommendPropertiesArgs(budget_amount=7000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert sorted(_names(result)) == ["Villa 0", "Villa 1", "Villa 2"]
    assert result.near_budget == []


# --- Tool wrapper end to end -------------------------------------------------------


async def test_the_live_call_payload_now_succeeds(db_session, test_user):
    """The exact payload from the live call -- `nights` is supported and the
    legacy `budget` name maps onto budget_amount; the guest's own words
    supply the basis."""
    await _property(db_session, test_user, name="Lake Haveli", base_price=6500)
    state = ConversationState()
    params = _FakeFunctionCallParams(guest_turns=["mera budget ₹7000 per night hai"])
    await _tools(state, test_user.id)["recommend_properties"](
        params, budget=7000, nights=1, num_guests=2, preferred_location="Udaipur"
    )
    assert params.envelope["success"] is True and params.envelope["status"] == "ok"
    assert "Lake Haveli" in params.result
    assert state.slots["budget"] == 7000 and state.slots["budget_basis"] == "per_night"


async def test_udaipur_two_nights_7000_per_night_scenario(db_session, test_user):
    """ "I need a place in Udaipur for two nights. My budget is ₹7,000 per
    night." -- location, guests and budget all applied together."""
    await _property(db_session, test_user, name="Udaipur Pool Villa", base_price=6500, amenity_tags=["pool"])
    await _property(db_session, test_user, name="Udaipur Palace", base_price=9500)
    await _property(db_session, test_user, name="Udaipur Studio", base_price=5000, max_guests=1)
    await _property(db_session, test_user, name="Goa Villa", city="Goa", base_price=6000)
    state = ConversationState()
    params = _FakeFunctionCallParams(
        guest_turns=["I need a place in Udaipur for two nights. My budget is ₹7,000 per night."]
    )
    await _tools(state, test_user.id)["recommend_properties"](
        params, budget_amount=7000, budget_basis="per_night", nights=2, num_guests=2, preferred_location="Udaipur"
    )
    assert "Udaipur Pool Villa" in params.result
    assert "Budget applied: ₹7,000 per night (₹14,000 for 2 nights)." in params.result
    for excluded in ("Udaipur Palace", "Udaipur Studio", "Goa Villa"):
        assert excluded not in params.result


async def test_explicit_phrasings_need_no_clarification(db_session, test_user):
    await _property(db_session, test_user, name="Lake Haveli", base_price=6000)
    for guest_said in ("₹7000 per night", "7k a night", "7000 nightly", "14000 for the whole stay"):
        amount = 14000 if "whole stay" in guest_said else 7000
        params = _FakeFunctionCallParams(guest_turns=[guest_said])
        await _tools(ConversationState(), test_user.id)["recommend_properties"](
            params, budget_amount=amount, nights=2
        )
        assert params.envelope["status"] == "ok", guest_said
        assert "Lake Haveli" in params.result, guest_said


async def test_ambiguous_budget_one_night_needs_no_question(db_session, test_user):
    await _property(db_session, test_user, name="Lake Haveli", base_price=6500)
    params = _FakeFunctionCallParams(guest_turns=["my budget is 7000"])
    await _tools(ConversationState(), test_user.id)["recommend_properties"](params, budget_amount=7000, nights=1)
    assert params.envelope["status"] == "ok"
    assert "Lake Haveli" in params.result


async def test_ambiguous_budget_multi_night_asks_exactly_once(db_session, test_user):
    await _property(db_session, test_user, name="Lake Haveli", base_price=6500)
    state = ConversationState()
    recommend = _tools(state, test_user.id)["recommend_properties"]

    params = _FakeFunctionCallParams(guest_turns=["my budget is 7000"])
    await recommend(params, budget_amount=7000, nights=2)
    assert params.envelope["status"] == "needs_clarification"
    assert "per night, or ₹7,000 for the entire stay?" in params.result
    assert "Lake Haveli" not in params.result
    assert "budget_basis" not in state.slots

    # The guest's answer didn't settle it -- assume per night, never re-ask.
    params = _FakeFunctionCallParams(guest_turns=["haan wahi"])
    await recommend(params, budget_amount=7000, nights=2)
    assert params.envelope["status"] == "ok"
    assert "Lake Haveli" in params.result
    assert "assumed per night" in params.result


async def test_ambiguous_budget_unknown_stay_length_asks_nights_first(db_session, test_user):
    await _property(db_session, test_user, name="Lake Haveli", base_price=6500)
    params = _FakeFunctionCallParams(guest_turns=["my budget is 7000"])
    await _tools(ConversationState(), test_user.id)["recommend_properties"](params, budget_amount=7000)
    assert params.envelope["status"] == "needs_clarification"
    assert "how many nights they're planning to stay" in params.result


async def test_established_per_night_is_never_asked_again(db_session, test_user):
    await _property(db_session, test_user, name="Lake Haveli", base_price=7800)
    state = ConversationState()
    recommend = _tools(state, test_user.id)["recommend_properties"]
    await recommend(_FakeFunctionCallParams(guest_turns=["7000 per night"]), budget_amount=7000, nights=2)
    assert state.slots["budget_basis"] == "per_night"

    params = _FakeFunctionCallParams(guest_turns=["okay make it 8000", "we are total 4 people"])
    await recommend(params, budget_amount=8000, nights=2)
    assert params.envelope["status"] == "ok"
    assert "Lake Haveli" in params.result
    assert state.slots["budget_basis"] == "per_night"


async def test_clarification_keeps_earlier_recommendations(db_session, test_user):
    """A clarification (or zero-match) result must not wipe
    recommendations_shown -- "something cheaper" is relative to them."""
    await _property(db_session, test_user, name="Lake Haveli", base_price=6500)
    state = ConversationState()
    recommend = _tools(state, test_user.id)["recommend_properties"]
    await recommend(_FakeFunctionCallParams(), preferred_location="Udaipur")
    shown = list(state.recommendations_shown)
    assert shown

    params = _FakeFunctionCallParams(guest_turns=["budget is 20000"])
    await recommend(params, budget_amount=20000, nights=3)
    assert params.envelope["status"] == "needs_clarification"
    assert state.recommendations_shown == shown


async def test_something_cheaper_still_works_after_a_stated_budget(db_session, test_user):
    await _property(db_session, test_user, name="Mid Villa", base_price=6000)
    await _property(db_session, test_user, name="Cheap Villa", base_price=4000)
    state = ConversationState()
    state.set_slot("budget", 7000)
    state.set_slot("budget_basis", "per_night")
    state.record_recommendations([{"property_id": "x", "name": "Mid Villa", "price": 6000, "guests": 2}])
    params = _FakeFunctionCallParams()
    await _tools(state, test_user.id)["recommend_properties"](params, cheaper_than_shown=True, nights=2)
    assert "Cheap Villa" in params.result
    assert "Mid Villa" not in params.result
    assert state.slots["budget"] == 7000  # the derived ceiling is never persisted


async def test_update_lead_budget_basis_is_backfilled_into_recommend(db_session, test_user):
    await _property(db_session, test_user, name="Lake Haveli", base_price=6500)
    state = ConversationState()
    tools = _tools(state, test_user.id)
    await tools["update_lead"](_FakeFunctionCallParams(), budget_amount=14000, budget_basis="total_stay", nights=2)
    params = _FakeFunctionCallParams()
    await tools["recommend_properties"](params)
    assert "₹14,000 total for 2 nights" in params.result
    assert "Lake Haveli" in params.result


async def test_pitched_offer_is_what_get_pricing_quotes(db_session, test_user):
    """Once the offer is pitched, get_pricing's first quote is the offer
    price -- the guest never hears a higher number than the one pitched."""
    villa = await _property(db_session, test_user, name="Offer Villa", base_price=7500)
    await _stay_offer(db_session, villa, percent=10, min_nights=2)
    state = ConversationState()
    tools = _tools(state, test_user.id)
    await tools["recommend_properties"](
        _FakeFunctionCallParams(), budget_amount=7000, budget_basis="per_night", nights=2
    )
    assert state.recommendations_shown[0]["offer_percent"] == 10

    params = _FakeFunctionCallParams()
    await tools["get_pricing"](params, property_id=str(villa.id), num_guests=2, nights=2)
    assert "₹13,500 total" in params.result


async def test_without_a_pitched_offer_get_pricing_keeps_the_standard_first_quote(db_session, test_user):
    villa = await _property(db_session, test_user, name="Offer Villa", base_price=7500)
    await _stay_offer(db_session, villa, percent=10, min_nights=2)
    params = _FakeFunctionCallParams()
    await _tools(ConversationState(), test_user.id)["get_pricing"](
        params, property_id=str(villa.id), num_guests=2, nights=2
    )
    assert "₹15,000 total" in params.result


def test_tool_schema_matches_args_model_exactly():
    recommend = _tools(ConversationState(), uuid.uuid4())["recommend_properties"]
    schema = DirectFunctionWrapper(recommend).to_function_schema()
    assert set(schema.properties) == set(RecommendPropertiesArgs.model_fields)
    assert "nights" in schema.properties and "budget" not in schema.properties


async def test_unsupported_argument_is_rejected_not_silently_dropped(db_session, test_user):
    await _property(db_session, test_user, name="Lake Haveli", base_price=6500)
    params = _FakeFunctionCallParams()
    await _tools(ConversationState(), test_user.id)["recommend_properties"](
        params, preferred_location="Udaipur", check_in="2026-10-01"
    )
    assert params.envelope["error_type"] == "invalid_tool_arguments"
    assert "'check_in' is not a parameter" in params.result
    assert "Lake Haveli" not in params.result


# --- Failure vs empty -----------------------------------------------------------------


async def test_backend_failure_yields_internal_error_and_no_properties(db_session, test_user, monkeypatch):
    shown = await _property(db_session, test_user, name="Lake Haveli", base_price=6500)

    async def _boom(*args, **kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(tool_handlers, "handle_recommend_properties", _boom)
    state = ConversationState()
    guard = PropertyRecommendationGuardProcessor()
    params = _FakeFunctionCallParams()
    await _tools(state, test_user.id, guard=guard)["recommend_properties"](
        params, budget_amount=7000, budget_basis="per_night", nights=2
    )
    assert params.envelope["success"] is False and params.envelope["error_type"] == "internal_error"
    assert "NOT an empty result" in params.result
    assert shown.name not in params.result and "₹" not in params.result
    assert state.recommendations_shown == []

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[
            _call_started(),
            LLMFullResponseStartFrame(),
            LLMTextFrame("There are no properties under 7,000 -- but Lake Haveli is 6,500 rupees a night."),
            LLMFullResponseEndFrame(),
        ],
    )
    spoken = _spoken(down_frames)
    assert "Lake Haveli" not in spoken and "no properties" not in spoken
    assert "not able to pull up the property options" in spoken


async def test_failure_override_survives_a_tool_only_response():
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("recommend_properties", recommendation_failed_result())
    down_frames, _ = await run_test(
        guard,
        frames_to_send=[
            _call_started(),
            LLMFullResponseStartFrame(),
            LLMFullResponseEndFrame(),  # e.g. a response that only called escalate_to_host
            LLMFullResponseStartFrame(),
            LLMTextFrame("Lake Haveli is a lovely option at 6,500 a night."),
            LLMFullResponseEndFrame(),
        ],
    )
    spoken = _spoken(down_frames)
    assert "Lake Haveli" not in spoken
    assert "not able to pull up the property options" in spoken


def test_failure_and_empty_search_render_differently():
    failed = render_recommendation_text(recommendation_failed_result())
    empty = render_recommendation_text(RecommendationResult(options=[], not_found=True))
    empty_with_budget = render_recommendation_text(
        RecommendationResult(options=[], not_found=True, budget=normalize_budget(7000, "per_night", 2))
    )
    assert "system error" in failed
    assert "couldn't find a property" in empty
    assert "offer to widen the budget" in empty_with_budget


async def test_only_properties_the_tool_returned_can_be_spoken():
    real = PropertyCard(
        property_id=uuid.uuid4(), spoken_name="Lake Haveli", display_name="Lake Haveli", city="Udaipur",
        property_type=None, bedroom_count=None, base_price=6500, max_guests=2, top_amenities=[], usp=None,
        match_reasons=[], comparison_note="", is_premium=False, amenity_checklist="",
    )
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("recommend_properties", RecommendationResult(options=[real]))
    down_frames, _ = await run_test(
        guard,
        frames_to_send=[
            _call_started(),
            LLMFullResponseStartFrame(),
            LLMTextFrame("I'd suggest the Invented Palace at 6,000 rupees a night."),
            LLMFullResponseEndFrame(),
        ],
    )
    spoken = _spoken(down_frames)
    assert "Invented Palace" not in spoken and "Lake Haveli" in spoken


# --- Upsell: one better-but-steeper option, pitched with its offer ---------------


async def test_premium_property_over_budget_is_pitched_as_the_steeper_option(db_session, test_user):
    await _property(db_session, test_user, name="Lake Haveli", base_price=6500)
    await _property(db_session, test_user, name="Palace Suite", base_price=9000, is_premium=True)
    args = RecommendPropertiesArgs(budget_amount=7000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert _names(result) == ["Lake Haveli"]
    assert result.upsell is not None and result.upsell.spoken_name == "Palace Suite"
    text = render_recommendation_text(result)
    assert "STEEPER OPTION" in text
    assert "one of the host's premium properties" in text
    assert "for ₹9,000 a night (₹18,000 for your 2 nights)" in text
    assert "₹2,000 a night over their budget" in text


async def test_steeper_option_leads_with_its_offer_in_the_first_pitch(db_session, test_user):
    palace = await _property(db_session, test_user, name="Palace Suite", base_price=10000, is_premium=True)
    await _stay_offer(db_session, palace, percent=10, min_nights=2)
    await _property(db_session, test_user, name="Lake Haveli", base_price=6500)
    args = RecommendPropertiesArgs(budget_amount=7000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    text = render_recommendation_text(result)
    assert "Lead with the offer" in text
    assert "come to ₹18,000 instead of ₹20,000" in text
    assert "₹2,000 a night over their budget" in text  # measured on the offer price


async def test_no_steeper_option_without_a_real_reason_it_is_better(db_session, test_user):
    await _property(db_session, test_user, name="Lake Haveli", base_price=6500)
    await _property(db_session, test_user, name="Plain Pricier", base_price=9000)
    args = RecommendPropertiesArgs(budget_amount=7000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert result.upsell is None
    assert "STEEPER OPTION" not in render_recommendation_text(result)


async def test_no_steeper_option_far_beyond_the_budget(db_session, test_user):
    await _property(db_session, test_user, name="Lake Haveli", base_price=6500)
    await _property(db_session, test_user, name="Royal Palace", base_price=12000, is_premium=True)
    args = RecommendPropertiesArgs(budget_amount=7000, budget_basis="per_night", nights=2)
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert result.upsell is None  # 12,000 > 1.5 x 7,000


async def test_steeper_option_can_be_the_one_with_what_the_guest_asked_for(db_session, test_user):
    await _property(db_session, test_user, name="Lake Haveli", base_price=6500)
    await _property(db_session, test_user, name="Pool Villa", base_price=8500, amenity_tags=["pool"])
    args = RecommendPropertiesArgs(
        budget_amount=7000, budget_basis="per_night", nights=2, required_amenities=["private pool"]
    )
    result = await orchestrator.recommend_properties(db_session, args, test_user.id)
    assert _names(result) == ["Lake Haveli"]
    assert result.upsell is not None and result.upsell.spoken_name == "Pool Villa"
    assert "it has the pool they asked for" in render_recommendation_text(result)


async def test_steeper_option_offer_carries_through_to_get_pricing(db_session, test_user):
    palace = await _property(db_session, test_user, name="Palace Suite", base_price=10000, is_premium=True)
    await _stay_offer(db_session, palace, percent=10, min_nights=2)
    await _property(db_session, test_user, name="Lake Haveli", base_price=6500)
    state = ConversationState()
    tools = _tools(state, test_user.id)
    params = _FakeFunctionCallParams()
    await tools["recommend_properties"](params, budget_amount=7000, budget_basis="per_night", nights=2)
    assert params.envelope["status"] == "ok"
    assert "Palace Suite" in {o["name"] for o in state.recommendations_shown}

    params = _FakeFunctionCallParams()
    await tools["get_pricing"](params, property_id=str(palace.id), num_guests=2, nights=2)
    assert "₹18,000 total" in params.result


async def test_negotiation_never_stacks_on_a_pitched_offer(db_session, test_user):
    """The offer given in the first pitch IS the discount -- a pushback
    doesn't unlock a second one on top."""
    villa = await _property(db_session, test_user, name="Offer Villa", base_price=7500)
    await _stay_offer(db_session, villa, percent=10, min_nights=2)
    state = ConversationState()
    tools = _tools(state, test_user.id)
    await tools["recommend_properties"](
        _FakeFunctionCallParams(), budget_amount=7000, budget_basis="per_night", nights=2
    )

    params = _FakeFunctionCallParams()
    await tools["negotiate_rate"](params, property_id=str(villa.id), nights=2, guest_offer=12000)
    assert "already includes the 10% longer-stay offer" in params.result
    assert "₹13,500" in params.result

    params = _FakeFunctionCallParams()
    await tools["negotiate_rate"](params, property_id=str(villa.id), nights=2, guest_offer=13500)
    assert "works" in params.result


async def test_a_basis_is_never_stored_without_a_budget_amount(db_session, test_user):
    await _property(db_session, test_user, name="Lake Haveli", base_price=6500)
    state = ConversationState()
    params = _FakeFunctionCallParams(guest_turns=["how much is it per night?"])
    await _tools(state, test_user.id)["recommend_properties"](params, preferred_location="Udaipur")
    assert "budget_basis" not in state.slots


# --- Renderer / prompt regressions ------------------------------------------------


def _card(name: str, price: float = 6500) -> PropertyCard:
    return PropertyCard(
        property_id=uuid.uuid4(), spoken_name=name, display_name=name, city="Udaipur",
        property_type=None, bedroom_count=None, base_price=price, max_guests=2, top_amenities=[], usp=None,
        match_reasons=[], comparison_note="", is_premium=False, amenity_checklist="",
    )


def test_an_empty_result_is_never_rendered_as_blank_text():
    assert "couldn't find a property" in render_recommendation_text(RecommendationResult(options=[]))


def test_combo_note_stays_attached_to_the_options_not_a_stretch_line():
    result = RecommendationResult(
        options=[_card("Unit A")],
        combo_note=" Book two together.",
        near_budget=[_card("Stretch Villa", 7500)],
        budget=normalize_budget(7000, "per_night", 2),
    )
    text = render_recommendation_text(result)
    assert text.index("Book two together.") < text.index("Stretch Villa")


def test_prompt_budget_label_never_invites_a_repeat_basis_question():
    from app.voice.state_prompt_sync import _format_slots

    state = ConversationState()
    state.set_slot("budget", 7000)
    assert "only ask if recommend_properties asks" in _format_slots(state)
    state.budget_basis_clarification_asked = True
    assert "assumed per night -- don't ask again" in _format_slots(state)
    state.set_slot("budget_basis", "per_night")
    assert "~₹7,000 per night" in _format_slots(state)
