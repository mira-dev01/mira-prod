"""Phase 4 (generalized policy-driven negotiation architecture): unit tests
for app/services/negotiation_policy.py -- the pure policy-evaluation layer
extracted from pricing_engine.py's previously-inline resolution logic.

Deliberately DB-free (plain in-memory NegotiationRule objects, never added
to a session) -- this module takes already-fetched rule lists and returns
structured decisions with no I/O of its own, so its own tests shouldn't need
a real database either. Existing DB-fixture-driven tests
(test_negotiate_rate_host_policy.py, test_pricing_engine.py) continue to
cover the end-to-end path (DB -> pricing_engine -> negotiation_policy) and
all 41 of them pass unchanged against this refactor -- see the Phase 4 final
report for the full regression confirmation.

Test matrix maps directly onto the Phase 4 brief's Step 18 cases -- only the
cases this phase's actual (bounded, non-tiered) scope can address. Cases
D/E/F (multi-stage progression), K (genuine cross-rule-type conflict
precedence), and O (mid-negotiation re-evaluation) are explicitly NOT
covered here -- see the final report's "Generalization Gaps"/"Remaining
Product Decisions" sections for why each is a real, unresolved product
decision rather than an oversight.
"""

import uuid

import pytest

from app.models.negotiation_rule import NegotiationRule
from app.services.negotiation_policy import (
    REASON_CUSTOM,
    REASON_GUEST_REQUESTS,
    REASON_REPEAT_GUEST,
    GuestNegotiationContext,
    resolve_custom_property_ceiling,
    resolve_discount_trigger,
    resolve_rule_type_to_best_staged_or_flat_value,
    resolve_rule_type_to_best_value,
    resolve_stage_index,
    resolve_staged_value,
)
from app.voice.conversation_state import NegotiationEvent


def _rule(rule_type: str, discount_percent: float | None, status: str = "approved", **kwargs) -> NegotiationRule:
    return NegotiationRule(
        id=uuid.uuid4(),
        host_id=uuid.uuid4(),
        rule_type=rule_type,
        discount_percent=discount_percent,
        condition=kwargs.pop("condition", {}),
        property_ids=kwargs.pop("property_ids", []),
        status=status,
        **kwargs,
    )


_NEW_GUEST = GuestNegotiationContext(is_repeat_guest=False)
_REPEAT_GUEST = GuestNegotiationContext(is_repeat_guest=True)


# ---------------------------------------------------------------------------
# Case A: no negotiation policy at all -> no discount authorized.
# ---------------------------------------------------------------------------


def test_case_a_no_rules_authorizes_nothing():
    assert resolve_discount_trigger([], _NEW_GUEST) is None
    assert resolve_discount_trigger([], _REPEAT_GUEST) is None
    assert resolve_custom_property_ceiling([]) is None


# ---------------------------------------------------------------------------
# Case B: a single fixed discount -> only that configured value, nothing
# invented on top of it.
# ---------------------------------------------------------------------------


def test_case_b_single_fixed_discount_is_used_exactly_as_configured():
    rules = [_rule("discount_guest_requests", 5)]
    decision = resolve_discount_trigger(rules, _NEW_GUEST)
    assert decision is not None
    assert decision.percent == 5.0
    assert decision.reason == REASON_GUEST_REQUESTS
    assert decision.source_rule_ids == (rules[0].id,)


# ---------------------------------------------------------------------------
# Case C: a maximum/cap-only policy -> that maximum is respected without the
# engine inventing an opening percentage below it. resolve_rule_type_to_best_value
# itself never fabricates a smaller "first offer" -- it returns exactly the
# host-approved value, full stop; the "no invented tiers" property is
# structural (there is no code path that could produce a number the host
# didn't configure), not something a single test run can prove probabilistically.
# ---------------------------------------------------------------------------


def test_case_c_single_value_is_the_whole_authorization_no_invented_floor():
    rules = [_rule("discount_guest_requests", 12)]
    decision = resolve_discount_trigger(rules, _NEW_GUEST)
    assert decision is not None
    assert decision.percent == 12.0  # exactly the configured ceiling, nothing smaller synthesized


# ---------------------------------------------------------------------------
# Cases D/E/F (multi-stage progression, different tier counts, different
# tier values) are NOT implemented -- see this module's own docstring and
# the Phase 4 final report. No test asserts progression behavior, since
# asserting it would require inventing the exact schema/semantics this
# phase's investigation found to be a genuine, unresolved product decision.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Case G: repeat-guest policy with one configured benefit -> applies only
# when the guest is actually eligible.
# ---------------------------------------------------------------------------


def test_case_g_repeat_guest_benefit_applies_only_when_eligible():
    rules = [_rule("discount_repeat_guest", 5)]

    eligible = resolve_discount_trigger(rules, _REPEAT_GUEST)
    assert eligible is not None
    assert eligible.percent == 5.0
    assert eligible.reason == REASON_REPEAT_GUEST

    not_eligible = resolve_discount_trigger(rules, _NEW_GUEST)
    assert not_eligible is None  # no discount_guest_requests rule either, so nothing applies


# ---------------------------------------------------------------------------
# Case H: repeat-guest policy absent -> no automatic repeat-guest discount,
# even for a genuinely repeat guest.
# ---------------------------------------------------------------------------


def test_case_h_no_repeat_guest_policy_means_no_automatic_benefit():
    rules = [_rule("discount_guest_requests", 5)]  # a DIFFERENT trigger type exists, repeat_guest does not

    decision = resolve_discount_trigger(rules, _REPEAT_GUEST)
    assert decision is not None
    assert decision.reason == REASON_GUEST_REQUESTS  # falls through to the ask-based trigger, not a repeat-guest one
    assert decision.percent == 5.0


def test_case_h_repeat_guest_with_zero_rules_gets_nothing():
    decision = resolve_discount_trigger([], _REPEAT_GUEST)
    assert decision is None


# ---------------------------------------------------------------------------
# Case I: property-specific policy (rule_type="custom") -> only applies to
# the property it was actually scoped/fetched for. This module trusts its
# caller (pricing_engine._approved_property_pricing_rules) to have already
# filtered by property_ids -- confirmed by direct read of that function
# during Phase 4's investigation -- so this test proves
# resolve_custom_property_ceiling behaves correctly GIVEN an
# already-property-scoped list, matching the existing caller contract.
# ---------------------------------------------------------------------------


def test_case_i_custom_property_ceiling_resolves_from_a_pre_scoped_list():
    property_id = str(uuid.uuid4())
    rules = [_rule("custom", 20, property_ids=[property_id])]

    decision = resolve_custom_property_ceiling(rules)
    assert decision is not None
    assert decision.percent == 20.0
    assert decision.reason == REASON_CUSTOM


# ---------------------------------------------------------------------------
# Case J: host-wide policy applies according to scope -- discount_* trigger
# types are host-wide BY DEFINITION regardless of property_ids (see
# NegotiationRule's own docstring, confirmed in Phase 4's investigation) --
# this module never filters discount triggers by property at all, matching
# that documented scope rule exactly.
# ---------------------------------------------------------------------------


def test_case_j_discount_trigger_is_host_wide_regardless_of_property_ids():
    rules = [_rule("discount_guest_requests", 5, property_ids=[])]  # empty = host-wide, per the model's own docstring
    decision = resolve_discount_trigger(rules, _NEW_GUEST)
    assert decision is not None
    assert decision.percent == 5.0


# ---------------------------------------------------------------------------
# Case K: conflicting policies. The ONLY defined precedence in the current
# product is the existing, pre-Phase-4 "most generous wins" resolution
# within ONE rule_type (see resolve_rule_type_to_best_value's own docstring) --
# made explicit here, not newly invented. A genuine CROSS-rule-type conflict
# (e.g. would a repeat-guest benefit and a guest-requests benefit ever
# authorize mutually exclusive, non-additive actions?) has no defined
# precedence in the current product -- this test explicitly documents that
# gap rather than asserting invented behavior, per the brief's own Step 5
# instruction ("If precedence is undefined: STOP... report the product
# decision required").
# ---------------------------------------------------------------------------


def test_case_k_same_rule_type_duplicate_conflict_uses_most_generous_wins():
    """The one precedence rule that DOES exist today, confirmed unchanged
    from pre-Phase-4 behavior by test_negotiate_rate_host_policy.py's own
    passing suite."""
    lower = _rule("discount_guest_requests", 5)
    higher = _rule("discount_guest_requests", 7)
    resolved = resolve_rule_type_to_best_value([lower, higher], "discount_guest_requests")
    assert resolved is not None
    percent, winner_ids = resolved
    assert percent == 7.0
    assert winner_ids == (higher.id,)


def test_case_k_cross_rule_type_precedence_is_the_documented_existing_behavior_not_invented():
    """repeat_guest winning over guest_requests for an eligible repeat guest
    is EXISTING pre-Phase-4 behavior (originally an if/elif chain inline in
    pricing_engine.negotiate_rate) -- resolve_discount_trigger is now the
    ONE live implementation of it (negotiate_rate calls this function
    directly), not a second, parallel copy of the same precedence rule.
    There is no test asserting behavior for any rule-type combination
    beyond the ones the current schema actually supports, since no such
    combination exists to conflict."""
    rules = [_rule("discount_repeat_guest", 5), _rule("discount_guest_requests", 9)]
    decision = resolve_discount_trigger(rules, _REPEAT_GUEST)
    assert decision is not None
    assert decision.reason == REASON_REPEAT_GUEST
    assert decision.percent == 5.0  # repeat-guest's own value, NOT the higher guest_requests value


# ---------------------------------------------------------------------------
# Case L: unapproved policy -> ignored. This module only ever receives
# already-approved rules from its callers (pricing_engine._approved_negotiation_rules/
# _approved_property_pricing_rules both filter status=="approved" before this
# module ever sees a row) -- confirmed by direct read during Phase 4's
# investigation. This test proves the module ITSELF has no separate
# opinion on status (it doesn't re-check it), so the approval gate's
# integrity depends entirely on callers continuing to filter before calling
# in, matching the existing, unchanged contract.
# ---------------------------------------------------------------------------


def test_case_l_module_does_not_itself_filter_by_status_callers_must():
    """Documents the actual contract: passing a pending_validation rule
    directly WOULD be honored by this module (it has no status field to
    check) -- the real guarantee that unapproved rules never influence
    negotiation lives in pricing_engine.py's _approved_negotiation_rules/
    _approved_property_pricing_rules DB queries, which this module's own
    callers always go through first. Existing end-to-end test coverage
    (test_negotiate_rate_host_policy.py::test_pending_validation_rule_is_not_used)
    proves the REAL guarantee holds; this test documents WHY -- so a future
    reader doesn't assume this module re-validates status and skip that
    upstream filter by mistake."""
    pending_rule = _rule("discount_guest_requests", 50, status="pending_validation")
    decision = resolve_discount_trigger([pending_rule], _NEW_GUEST)
    assert decision is not None
    assert decision.percent == 50.0  # honored -- proves callers, not this module, own the approval gate


# ---------------------------------------------------------------------------
# Case M: duplicate semantic policies -> no accidental multiplication or
# escalation. Two approved rules of the same type never sum or compound --
# resolve_rule_type_to_best_value takes the single best value, never adds them.
# ---------------------------------------------------------------------------


def test_case_m_duplicate_rules_never_sum_or_multiply():
    rules = [_rule("discount_guest_requests", 5), _rule("discount_guest_requests", 5)]
    resolved = resolve_rule_type_to_best_value(rules, "discount_guest_requests")
    assert resolved is not None
    percent, winner_ids = resolved
    assert percent == 5.0  # NOT 10.0 -- duplicates at the same value never compound
    assert len(winner_ids) == 2  # but both are reported as sources, for a future audit/dedup layer to see


def test_case_m_three_duplicate_rules_at_different_values_still_resolve_to_one_winner():
    rules = [_rule("discount_guest_requests", 3), _rule("discount_guest_requests", 5), _rule("discount_guest_requests", 5)]
    resolved = resolve_rule_type_to_best_value(rules, "discount_guest_requests")
    assert resolved is not None
    percent, winner_ids = resolved
    assert percent == 5.0
    assert len(winner_ids) == 2  # only the two rules actually AT the winning value


# ---------------------------------------------------------------------------
# Case N: separate calls / call sessions cannot affect each other. This
# module takes rule lists as plain function arguments with zero shared
# module-level or global state -- confirmed structurally: no caching,
# no mutable module-level dict, every call is a pure function of its inputs.
# ---------------------------------------------------------------------------


def test_case_n_module_has_no_shared_state_between_calls():
    rules_a = [_rule("discount_guest_requests", 5)]
    rules_b = [_rule("discount_guest_requests", 9)]

    decision_a = resolve_discount_trigger(rules_a, _NEW_GUEST)
    decision_b = resolve_discount_trigger(rules_b, _NEW_GUEST)
    # A second, unrelated call's rules must not leak into or influence the
    # first call's already-returned decision (proves no shared mutable
    # state was consulted or mutated in between).
    assert decision_a.percent == 5.0
    assert decision_b.percent == 9.0
    assert decision_a.percent == 5.0  # re-checked after decision_b -- would fail if state leaked


# ---------------------------------------------------------------------------
# Case O: guest changes booking parameters mid-negotiation. This module is
# stateless per call (Case N) -- a changed property_id/dates simply means
# pricing_engine.negotiate_rate (the caller) re-fetches rules and calls in
# again with fresh inputs, which this module always evaluates fresh, never
# from a cached prior decision. There is no explicit "re-evaluation trigger"
# concept here because none is needed: statelessness means every call IS a
# fresh evaluation. Full mid-conversation re-evaluation semantics (should a
# guest's THIRD ask see different behavior than their first) are part of
# the Case D/E/F progression gap, not solved here.
# ---------------------------------------------------------------------------


def test_case_o_different_property_scoped_rules_evaluate_independently():
    property_a_rules = [_rule("custom", 10)]
    property_b_rules = [_rule("custom", 25)]

    decision_a = resolve_custom_property_ceiling(property_a_rules)
    decision_b = resolve_custom_property_ceiling(property_b_rules)

    assert decision_a.percent == 10.0
    assert decision_b.percent == 25.0


# ---------------------------------------------------------------------------
# Additional structural guards -- not from the brief's numbered list, but
# direct regression coverage for specific bugs a naive reimplementation
# could introduce.
# ---------------------------------------------------------------------------


def test_rules_with_none_discount_percent_are_never_matched():
    """A NegotiationRule row can have discount_percent=None (e.g. a
    minimum_stay_nights rule, which has no discount action at all) --
    confirmed via the model's own nullable column. Must never be treated as
    a 0% or any other numeric match."""
    rules = [_rule("discount_guest_requests", None)]
    assert resolve_rule_type_to_best_value(rules, "discount_guest_requests") is None
    assert resolve_discount_trigger(rules, _NEW_GUEST) is None


def test_unrelated_rule_types_never_leak_into_a_resolution():
    """A minimum_stay_nights or length_of_stay rule must never be picked up
    by discount-trigger resolution, even if it happens to have a
    discount_percent value set (length_of_stay does)."""
    rules = [_rule("length_of_stay", 15), _rule("minimum_stay_nights", None)]
    assert resolve_discount_trigger(rules, _NEW_GUEST) is None
    assert resolve_custom_property_ceiling(rules) is None


def test_repeat_guest_eligible_but_only_guest_requests_rule_approved_falls_through_correctly():
    """A repeat guest with NO discount_repeat_guest rule approved, but a
    discount_guest_requests rule that IS approved, still gets the
    guest_requests benefit -- being a repeat guest never SUPPRESSES an
    otherwise-applicable benefit, it only adds priority when both exist."""
    rules = [_rule("discount_guest_requests", 6)]
    decision = resolve_discount_trigger(rules, _REPEAT_GUEST)
    assert decision is not None
    assert decision.reason == REASON_GUEST_REQUESTS
    assert decision.percent == 6.0


# ---------------------------------------------------------------------------
# Phase 4D: generalized staged-negotiation additions. Every stage
# count/value below is deliberately arbitrary and varied across tests --
# no test uses a single "canonical" ladder, per the phase's own
# no-host-overfitting constraint. All resolve_stage_index examples mirror
# the Phase 4D brief's own worked examples exactly (Step 4).
# ---------------------------------------------------------------------------


def test_stage_index_zero_events_is_stage_zero():
    assert resolve_stage_index([], 3) == 0


def test_stage_index_first_numeric_offer_cannot_skip_stage_zero():
    """Ratified Phase 4C decision: a large first numeric offer still
    evaluates against stage 0, never a later stage."""
    events = [NegotiationEvent(guest_offer=999999, property_id="p1")]
    assert resolve_stage_index(events, 5) == 0


def test_stage_index_same_offer_repeated_does_not_progress():
    events = [
        NegotiationEvent(guest_offer=4000, property_id="p1"),
        NegotiationEvent(guest_offer=4000, property_id="p1"),
    ]
    assert resolve_stage_index(events, 3) == 0


def test_stage_index_lower_offer_does_not_progress():
    events = [
        NegotiationEvent(guest_offer=4000, property_id="p1"),
        NegotiationEvent(guest_offer=3900, property_id="p1"),
    ]
    assert resolve_stage_index(events, 3) == 0


def test_stage_index_strictly_higher_offer_progresses_exactly_one_stage():
    events = [
        NegotiationEvent(guest_offer=4000, property_id="p1"),
        NegotiationEvent(guest_offer=4200, property_id="p1"),
    ]
    assert resolve_stage_index(events, 3) == 1


def test_stage_index_unquantified_offer_never_progresses():
    events = [
        NegotiationEvent(guest_offer=4000, property_id="p1"),
        NegotiationEvent(guest_offer=None, property_id="p1"),
    ]
    assert resolve_stage_index(events, 3) == 0


def test_stage_index_cannot_exceed_final_stage():
    """Arbitrary stage count (2) and arbitrary offer values -- clamps at
    stage_count - 1 regardless of how many further progressing offers
    follow."""
    events = [
        NegotiationEvent(guest_offer=100, property_id="p1"),
        NegotiationEvent(guest_offer=200, property_id="p1"),
        NegotiationEvent(guest_offer=300, property_id="p1"),
        NegotiationEvent(guest_offer=400, property_id="p1"),
    ]
    assert resolve_stage_index(events, 2) == 1  # only 2 stages exist: index 0 and 1


def test_stage_index_zero_stage_count_always_zero():
    events = [NegotiationEvent(guest_offer=5000, property_id="p1")]
    assert resolve_stage_index(events, 0) == 0


def test_stage_index_arbitrary_large_stage_count_and_values():
    """No assumption anywhere about a 'typical' stage count -- this uses 7
    stages with large, arbitrary rupee values to prove nothing hardcodes a
    small N."""
    events = [
        NegotiationEvent(guest_offer=10000, property_id="p1"),
        NegotiationEvent(guest_offer=15000, property_id="p1"),
        NegotiationEvent(guest_offer=22000, property_id="p1"),
    ]
    assert resolve_stage_index(events, 7) == 2


def test_resolve_staged_value_reads_arbitrary_ordered_stages():
    rule = _rule("discount_guest_requests", None, stages=[{"order": 1, "value": 8}, {"order": 0, "value": 3}])
    assert resolve_staged_value(rule, 0) == 3.0
    assert resolve_staged_value(rule, 1) == 8.0


def test_resolve_staged_value_clamps_out_of_range_index():
    rule = _rule("discount_guest_requests", None, stages=[{"order": 0, "value": 5}])
    assert resolve_staged_value(rule, 99) == 5.0  # clamped to the only stage that exists


def test_resolve_staged_value_none_when_no_stages_configured():
    rule = _rule("discount_guest_requests", 10, stages=None)
    assert resolve_staged_value(rule, 0) is None


def test_resolve_staged_value_skips_malformed_entries():
    rule = _rule("discount_guest_requests", None, stages=[{"order": 0, "value": "not a number"}, {"order": 1, "value": 9}])
    assert resolve_staged_value(rule, 0) == 9.0  # the malformed entry is skipped, not crashed on


def test_staged_rule_supersedes_flat_rule_same_type_ratified_decision():
    """Phase 4C ratified decision (Decisions Log item 3): a staged rule
    supersedes a flat rule of the same rule_type/scope -- never merged,
    never max()'d together."""
    flat = _rule("discount_guest_requests", 25)  # deliberately the LARGER raw number
    staged = _rule("discount_guest_requests", None, stages=[{"order": 0, "value": 3}])
    resolved = resolve_rule_type_to_best_staged_or_flat_value([flat, staged], "discount_guest_requests", 0)
    assert resolved is not None
    percent, ids, is_staged, stage_count = resolved
    assert is_staged is True
    assert percent == 3.0  # the staged rule's stage-0 value, NOT the flat rule's 25%
    assert ids == (staged.id,)
    assert stage_count == 1


def test_no_staged_rule_falls_back_to_identical_flat_behavior():
    """When no rule of this type has stages configured, behavior must be
    byte-identical to resolve_rule_type_to_best_value -- this is what keeps
    every existing flat-only host's behavior unchanged."""
    rules = [_rule("discount_guest_requests", 5), _rule("discount_guest_requests", 7)]
    flat_only = resolve_rule_type_to_best_value(rules, "discount_guest_requests")
    staged_aware = resolve_rule_type_to_best_staged_or_flat_value(rules, "discount_guest_requests", 0)
    assert staged_aware is not None
    percent, ids, is_staged, stage_count = staged_aware
    assert (percent, ids) == flat_only
    assert is_staged is False
    assert stage_count is None


def test_resolve_discount_trigger_is_staged_aware_and_defaults_to_stage_zero():
    rules = [_rule("discount_guest_requests", None, stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}])]
    decision = resolve_discount_trigger(rules, _NEW_GUEST)  # stage_index defaults to 0
    assert decision is not None
    assert decision.percent == 4.0
    assert decision.is_staged is True
    assert decision.stage_index == 0
    assert decision.stage_count == 2


def test_resolve_discount_trigger_staged_at_a_later_stage():
    rules = [_rule("discount_guest_requests", None, stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}])]
    decision = resolve_discount_trigger(rules, _NEW_GUEST, stage_index=1)
    assert decision is not None
    assert decision.percent == 9.0
    assert decision.stage_index == 1


def test_resolve_discount_trigger_repeat_guest_bypasses_staged_guest_requests_ladder():
    """Ratified Phase 4C decision (Decisions Log item 2): repeat-guest
    bypasses a staged guest_requests ladder entirely when an approved
    discount_repeat_guest rule applies -- the ladder is never consulted."""
    rules = [
        _rule("discount_guest_requests", None, stages=[{"order": 0, "value": 3}, {"order": 1, "value": 6}, {"order": 2, "value": 9}]),
        _rule("discount_repeat_guest", 12),  # flat, deliberately not staged -- proves this isn't a stage-vs-stage comparison
    ]
    decision = resolve_discount_trigger(rules, _REPEAT_GUEST, stage_index=2)
    assert decision is not None
    assert decision.reason == REASON_REPEAT_GUEST
    assert decision.percent == 12.0
    assert decision.is_staged is False  # the repeat-guest rule itself is flat


def test_resolve_discount_trigger_repeat_guest_rule_can_itself_be_staged():
    """discount_repeat_guest may independently be staged or flat -- this is
    a different question from whether it beats a guest_requests ladder
    (settled unconditionally by control flow, see the test above)."""
    rules = [_rule("discount_repeat_guest", None, stages=[{"order": 0, "value": 5}, {"order": 1, "value": 11}])]
    decision = resolve_discount_trigger(rules, _REPEAT_GUEST, stage_index=1)
    assert decision is not None
    assert decision.reason == REASON_REPEAT_GUEST
    assert decision.percent == 11.0
    assert decision.is_staged is True


def test_resolve_custom_property_ceiling_staged_arbitrary_property():
    property_id = str(uuid.uuid4())
    rules = [
        _rule(
            "custom",
            None,
            property_ids=[property_id],
            stages=[{"order": 0, "value": 6}, {"order": 1, "value": 18}, {"order": 2, "value": 27}],
        )
    ]
    decision = resolve_custom_property_ceiling(rules, stage_index=2)
    assert decision is not None
    assert decision.percent == 27.0
    assert decision.is_staged is True
    assert decision.stage_count == 3


def test_zero_stages_configured_is_mathematically_identical_to_flat():
    """Phase 4B Section H: a policy with an empty stages list behaves
    exactly as a flat policy -- stages=None and stages=[] both fall
    through to the flat path, never treated as 'staged with zero options'."""
    rule_empty_list = _rule("discount_guest_requests", 5, stages=[])
    rule_none = _rule("discount_guest_requests", 5, stages=None)
    resolved_empty = resolve_rule_type_to_best_staged_or_flat_value([rule_empty_list], "discount_guest_requests", 0)
    resolved_none = resolve_rule_type_to_best_staged_or_flat_value([rule_none], "discount_guest_requests", 0)
    assert resolved_empty == (5.0, (rule_empty_list.id,), False, None)
    assert resolved_none == (5.0, (rule_none.id,), False, None)


def test_one_stage_is_mathematically_identical_to_flat_value():
    """Phase 4B Section H: a policy with exactly one stage entry resolves
    to that one value regardless of stage_index passed in (clamped)."""
    rule = _rule("discount_guest_requests", None, stages=[{"order": 0, "value": 7}])
    for stage_index in (0, 1, 5):
        resolved = resolve_rule_type_to_best_staged_or_flat_value([rule], "discount_guest_requests", stage_index)
        assert resolved is not None
        percent, _, is_staged, stage_count = resolved
        assert percent == 7.0
        assert is_staged is True
        assert stage_count == 1
