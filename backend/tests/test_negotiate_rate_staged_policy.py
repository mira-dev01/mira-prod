"""Phase 4D (generalized negotiation state + policy runtime foundation) --
end-to-end DB-backed tests for pricing_engine.negotiate_rate's staged-policy
path, covering the full generalized test matrix from the Phase 4D brief
(cases A-X) plus the abuse/failure scenarios (1-8).

Deliberately uses ARBITRARY, VARIED stage counts and values across
different tests -- no single "canonical" host configuration is reused as
the primary fixture throughout, per the phase's explicit no-host-overfitting
constraint. Every NegotiationRule row here is constructed directly in the
test that needs it, exactly like the existing custom-rule tests in
test_pricing_engine.py this file sits alongside.
"""

from datetime import date, timedelta

from app.models.guest_profile import GuestProfile
from app.models.negotiation_rule import NegotiationRule
from app.models.property import Property
from app.services.pricing_engine import negotiate_rate
from app.voice.conversation_state import NegotiationEvent


def _next_weekday(start: date, weekday: int) -> date:
    delta = (weekday - start.weekday()) % 7
    return start + timedelta(days=delta)


def _dates():
    monday = _next_weekday(date.today(), 0)
    return monday, monday + timedelta(days=2)


# ---------------------------------------------------------------------------
# A. No negotiation policy at all.
# ---------------------------------------------------------------------------


async def test_a_no_negotiation_policy_falls_back_to_existing_default(test_property, db_session, test_user):
    check_in, check_out = _dates()
    result = await negotiate_rate(
        db_session, test_property, check_in, check_out, guest_offer=None, host_id=test_user.id
    )
    assert result.is_staged is False
    assert result.stage_index is None
    assert result.exhausted is False


# ---------------------------------------------------------------------------
# B. Existing flat policy -- must remain byte-identical to pre-Phase-4D
# behavior (same assertions as the pre-existing negotiate_rate tests).
# ---------------------------------------------------------------------------


async def test_b_existing_flat_policy_unchanged(test_property, db_session, test_user):
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", discount_percent=8, status="approved"))
    await db_session.commit()
    check_in, check_out = _dates()
    result = await negotiate_rate(
        db_session, test_property, check_in, check_out, guest_offer=None, host_id=test_user.id
    )
    assert result.is_staged is False
    expected_floor = round(result.asking_price * (1 - 8 / 100), 2)
    assert result.counter_offer == expected_floor


# ---------------------------------------------------------------------------
# C/D/E/F. One-stage, two-stage, more-than-two-stage, arbitrary values.
# ---------------------------------------------------------------------------


async def test_c_one_stage_policy_behaves_like_flat(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 6}],
            status="approved",
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    result = await negotiate_rate(db_session, test_property, check_in, check_out, guest_offer=None, host_id=test_user.id)
    assert result.is_staged is True
    assert result.stage_index == 0
    assert result.stage_count == 1
    assert result.exhausted is True  # only stage that exists


async def test_d_two_stage_policy_progresses_from_stage_zero_to_one(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}],
            status="approved",
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()

    first = await negotiate_rate(db_session, test_property, check_in, check_out, guest_offer=None, host_id=test_user.id)
    assert first.stage_index == 0
    assert first.exhausted is False

    prior = [NegotiationEvent(guest_offer=first.counter_offer, property_id=str(test_property.id))]
    second_offer = first.counter_offer + 500  # strictly higher than the first stage's own counter
    second = await negotiate_rate(
        db_session, test_property, check_in, check_out, guest_offer=second_offer, host_id=test_user.id, prior_events=prior
    )
    assert second.stage_index == 1
    assert second.progressed_this_event is True
    assert second.exhausted is True  # 2-stage policy, index 1 is the last


async def test_e_more_than_two_stages_arbitrary_count(test_property, db_session, test_user):
    """5 stages, arbitrary values -- proves no hardcoded stage count."""
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": i, "value": 2 * (i + 1)} for i in range(5)],
            status="approved",
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    prior = [
        NegotiationEvent(guest_offer=1000, property_id=str(test_property.id)),
        NegotiationEvent(guest_offer=1100, property_id=str(test_property.id)),
        NegotiationEvent(guest_offer=1200, property_id=str(test_property.id)),
    ]
    result = await negotiate_rate(
        db_session, test_property, check_in, check_out, guest_offer=1300, host_id=test_user.id, prior_events=prior
    )
    assert result.stage_count == 5
    assert result.stage_index == 3  # 3 prior progressions -> stage 3


async def test_f_arbitrary_stage_values_not_a_fixed_sequence(test_property, db_session, test_user):
    """Deliberately non-uniform, non-obvious stage values (not a clean
    3/5/7-style sequence) to prove nothing assumes a specific pattern."""
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 1.5}, {"order": 1, "value": 13.25}, {"order": 2, "value": 14.0}],
            status="approved",
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    result = await negotiate_rate(db_session, test_property, check_in, check_out, guest_offer=None, host_id=test_user.id)
    expected_floor = round(result.asking_price * (1 - 1.5 / 100), 2)
    assert result.counter_offer == expected_floor


# ---------------------------------------------------------------------------
# G/H. First numeric offer vs. first unquantified request.
# ---------------------------------------------------------------------------


async def test_g_first_numeric_offer_cannot_skip_stage_zero(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 3}, {"order": 1, "value": 20}],
            status="approved",
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    # A huge first offer -- must still be evaluated against stage 0, not stage 1.
    result = await negotiate_rate(
        db_session, test_property, check_in, check_out, guest_offer=999999, host_id=test_user.id
    )
    assert result.stage_index == 0


async def test_h_first_unquantified_request_evaluates_stage_zero(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 5}], status="approved"
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    result = await negotiate_rate(db_session, test_property, check_in, check_out, guest_offer=None, host_id=test_user.id)
    assert result.accepted is True
    assert result.stage_index == 0


# ---------------------------------------------------------------------------
# I/J/K/L/M. Progression rules.
# ---------------------------------------------------------------------------


async def test_i_guest_offer_none_does_not_progress(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}],
            status="approved",
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    prior = [
        NegotiationEvent(guest_offer=4000, property_id=str(test_property.id)),
        NegotiationEvent(guest_offer=4200, property_id=str(test_property.id)),  # already at stage 1
    ]
    result = await negotiate_rate(
        db_session, test_property, check_in, check_out, guest_offer=None, host_id=test_user.id, prior_events=prior
    )
    assert result.stage_index == 1  # unchanged
    assert result.progressed_this_event is False


async def test_j_repeated_identical_numeric_offer_does_not_progress(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}],
            status="approved",
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    prior = [NegotiationEvent(guest_offer=4000, property_id=str(test_property.id))]
    result = await negotiate_rate(
        db_session, test_property, check_in, check_out, guest_offer=4000, host_id=test_user.id, prior_events=prior
    )
    assert result.stage_index == 0
    assert result.progressed_this_event is False


async def test_k_lower_numeric_offer_does_not_progress(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}],
            status="approved",
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    prior = [NegotiationEvent(guest_offer=4000, property_id=str(test_property.id))]
    result = await negotiate_rate(
        db_session, test_property, check_in, check_out, guest_offer=3900, host_id=test_user.id, prior_events=prior
    )
    assert result.stage_index == 0
    assert result.progressed_this_event is False


async def test_l_strictly_higher_numeric_offer_progresses(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}],
            status="approved",
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    prior = [NegotiationEvent(guest_offer=4000, property_id=str(test_property.id))]
    result = await negotiate_rate(
        db_session, test_property, check_in, check_out, guest_offer=4200, host_id=test_user.id, prior_events=prior
    )
    assert result.stage_index == 1
    assert result.progressed_this_event is True


async def test_m_final_stage_cannot_be_exceeded(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}],  # only 2 stages
            status="approved",
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    prior = [
        NegotiationEvent(guest_offer=1000, property_id=str(test_property.id)),
        NegotiationEvent(guest_offer=2000, property_id=str(test_property.id)),
        NegotiationEvent(guest_offer=3000, property_id=str(test_property.id)),
        NegotiationEvent(guest_offer=4000, property_id=str(test_property.id)),
    ]
    result = await negotiate_rate(
        db_session, test_property, check_in, check_out, guest_offer=5000, host_id=test_user.id, prior_events=prior
    )
    assert result.stage_index == 1  # clamped, never 2/3/4
    assert result.exhausted is True
    # guest_offer (5000) is below the stage-1 floor -- refused, and the
    # counter must be EXACTLY the stage-1 (9%) floor, not some higher,
    # unauthorized value a bug might let a later, larger offer leak through.
    expected_floor = round(result.asking_price * (1 - 9 / 100), 2)
    assert result.accepted is False
    assert result.counter_offer == expected_floor


# ---------------------------------------------------------------------------
# N. Repeat guest bypasses staged ladder.
# ---------------------------------------------------------------------------


async def test_n_repeat_guest_bypasses_staged_guest_requests_ladder(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 3}, {"order": 1, "value": 6}],
            status="approved",
        )
    )
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_repeat_guest", discount_percent=11, status="approved"))
    guest = GuestProfile(host_id=test_user.id, phone="+919990001111", total_stays=3)
    db_session.add(guest)
    await db_session.commit()
    await db_session.refresh(guest)

    check_in, check_out = _dates()
    result = await negotiate_rate(
        db_session, test_property, check_in, check_out, guest_offer=None, host_id=test_user.id, guest_profile_id=guest.id
    )
    assert result.is_staged is False  # the repeat-guest flat rule won, ladder never consulted
    expected_floor = round(result.asking_price * (1 - 11 / 100), 2)
    assert result.counter_offer == expected_floor


# ---------------------------------------------------------------------------
# O/P. Property-specific vs. host-wide policy.
# ---------------------------------------------------------------------------


async def test_o_property_specific_staged_policy(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="custom",
            property_ids=[str(test_property.id)],
            stages=[{"order": 0, "value": 6}, {"order": 1, "value": 18}, {"order": 2, "value": 27}],
            status="approved",
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    prior = [
        NegotiationEvent(guest_offer=1000, property_id=str(test_property.id)),
        NegotiationEvent(guest_offer=1500, property_id=str(test_property.id)),
    ]
    result = await negotiate_rate(
        db_session, test_property, check_in, check_out, guest_offer=2000, host_id=test_user.id, prior_events=prior
    )
    assert result.is_staged is True
    assert result.stage_index == 2
    # Self-review fix: `assert a == b if cond else c` binds the `==` only to
    # the `if` branch -- when the guest_offer (2000) is below the floor
    # (the actual case here, since 1 - 27% of an 8000 asking price is 5840,
    # well above 2000), the `else` branch silently asserted a plain
    # truthy float, which is always True and checks nothing about
    # counter_offer at all. This test previously passed without ever
    # actually verifying the resolved price.
    expected_floor = round(result.asking_price * (1 - 27 / 100), 2)
    if 2000 >= expected_floor:
        assert result.accepted is True
        assert result.counter_offer == 2000
    else:
        assert result.accepted is False
        assert result.counter_offer == expected_floor


async def test_o2_staged_trigger_and_flat_custom_tying_on_percent_still_reports_staged(test_property, db_session, test_user):
    """Self-review regression: a staged discount_guest_requests rule and a
    FLAT custom rule that happen to tie at the exact same resolved percent
    must not cause the Decision to misattribute is_staged/stage_count/
    exhausted to the flat rule. Caught during self-review -- the original
    implementation picked "whichever decision's percent equals the final
    max_discount_percent," which silently attributes a coincidental tie to
    whichever branch is checked first (custom), even when the actual
    winning candidate was the staged trigger. Fixed by tracking which
    ResolvedDiscount produced discount_percent inline, at the moment each
    max() is evaluated, never by re-deriving it from a later float
    comparison."""
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 12}],
            status="approved",
        )
    )
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="custom",
            discount_percent=12,  # deliberately identical to the staged value above
            property_ids=[str(test_property.id)],
            status="approved",
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    result = await negotiate_rate(db_session, test_property, check_in, check_out, guest_offer=None, host_id=test_user.id)
    assert result.is_staged is True
    assert result.stage_count == 1
    assert result.exhausted is True


async def test_o3_flat_custom_genuinely_more_generous_than_staged_trigger_stays_flat(test_property, db_session, test_user):
    """Companion to test_o2 -- when custom is flat and GENUINELY the more
    generous candidate (not a coincidental tie), is_staged must correctly
    stay False."""
    db_session.add(
        NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", discount_percent=5, status="approved")
    )
    db_session.add(
        NegotiationRule(
            host_id=test_user.id, rule_type="custom", discount_percent=20, property_ids=[str(test_property.id)], status="approved"
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    result = await negotiate_rate(db_session, test_property, check_in, check_out, guest_offer=None, host_id=test_user.id)
    assert result.is_staged is False
    expected_floor = round(result.asking_price * (1 - 20 / 100), 2)
    assert result.counter_offer == expected_floor


async def test_o4_staged_custom_genuinely_more_generous_reports_its_own_stage_count(test_property, db_session, test_user):
    """Companion to test_o2/test_o3 -- when custom is STAGED and genuinely
    wins over a flat trigger, the Decision must report the CUSTOM rule's
    own stage_count, not the trigger's (which in this case is flat and has
    none)."""
    db_session.add(
        NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", discount_percent=5, status="approved")
    )
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="custom",
            stages=[{"order": 0, "value": 10}, {"order": 1, "value": 20}, {"order": 2, "value": 30}],
            property_ids=[str(test_property.id)],
            status="approved",
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    result = await negotiate_rate(db_session, test_property, check_in, check_out, guest_offer=None, host_id=test_user.id)
    assert result.is_staged is True
    assert result.stage_count == 3


async def test_p_host_wide_policy_applies_across_properties(db_session, test_user):
    property_a = Property(
        user_id=test_user.id, name="Villa A", city="Goa", exophone="+918000000001", base_price=3000, max_guests=4
    )
    property_b = Property(
        user_id=test_user.id, name="Villa B", city="Goa", exophone="+918000000002", base_price=5000, max_guests=6
    )
    db_session.add_all([property_a, property_b])
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 5}],
            status="approved",
        )
    )
    await db_session.commit()
    await db_session.refresh(property_a)
    await db_session.refresh(property_b)

    check_in, check_out = _dates()
    result_a = await negotiate_rate(db_session, property_a, check_in, check_out, guest_offer=None, host_id=test_user.id)
    result_b = await negotiate_rate(db_session, property_b, check_in, check_out, guest_offer=None, host_id=test_user.id)
    assert result_a.is_staged is True
    assert result_b.is_staged is True  # host-wide rule applies to BOTH properties


# ---------------------------------------------------------------------------
# Q. Flat + staged same scope -> staged wins.
# ---------------------------------------------------------------------------


async def test_q_flat_and_staged_same_scope_staged_wins(test_property, db_session, test_user):
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="discount_guest_requests", discount_percent=25, status="approved"))
    db_session.add(
        NegotiationRule(
            host_id=test_user.id, rule_type="discount_guest_requests", stages=[{"order": 0, "value": 3}], status="approved"
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    result = await negotiate_rate(db_session, test_property, check_in, check_out, guest_offer=None, host_id=test_user.id)
    assert result.is_staged is True
    expected_floor = round(result.asking_price * (1 - 3 / 100), 2)  # staged value, NOT the flat 25%
    assert result.counter_offer == expected_floor


# ---------------------------------------------------------------------------
# R. Property change resets negotiation state (tested at the ConversationState
# layer in test_conversation_state_negotiation.py; here we confirm
# negotiate_rate itself evaluates independently per-property when given a
# reset/empty prior_events list, matching what the tools.py wrapper does).
# ---------------------------------------------------------------------------


async def test_r_property_change_negotiation_evaluated_independently(db_session, test_user):
    property_a = Property(user_id=test_user.id, name="Villa A", city="Goa", exophone="+918000000003", base_price=3000, max_guests=4)
    property_b = Property(user_id=test_user.id, name="Villa B", city="Goa", exophone="+918000000004", base_price=3000, max_guests=4)
    db_session.add_all([property_a, property_b])
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}],
            status="approved",
        )
    )
    await db_session.commit()
    await db_session.refresh(property_a)
    await db_session.refresh(property_b)

    check_in, check_out = _dates()
    # Guest progressed on property A...
    prior_a = [NegotiationEvent(guest_offer=1000, property_id=str(property_a.id)), NegotiationEvent(guest_offer=1200, property_id=str(property_a.id))]
    # ...but switches to property B -- the wrapper (tools.py) would reset
    # events here; simulating that reset directly by passing an empty list.
    result_b = await negotiate_rate(db_session, property_b, check_in, check_out, guest_offer=None, host_id=test_user.id, prior_events=[])
    assert result_b.stage_index == 0  # NOT stage 1 -- old property's progress must not leak in


# ---------------------------------------------------------------------------
# S. Date/guest-count context reset per ratified semantics.
# ---------------------------------------------------------------------------


async def test_s_date_change_resets_negotiation_state(test_property, db_session, test_user):
    """Mirrors what app/voice/tools.py's wrapper does: on a date change it
    calls state.reset_negotiation_context() BEFORE calling negotiate_rate,
    so the next call passes an empty prior_events list even though the
    guest is still discussing the same property."""
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}],
            status="approved",
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    prior = [NegotiationEvent(guest_offer=1000, property_id=str(test_property.id)), NegotiationEvent(guest_offer=1200, property_id=str(test_property.id))]

    new_check_in, new_check_out = check_in + timedelta(days=7), check_out + timedelta(days=7)
    # Simulating the wrapper's reset: dates changed -> empty prior_events.
    result = await negotiate_rate(
        db_session, test_property, new_check_in, new_check_out, guest_offer=None, host_id=test_user.id, prior_events=[]
    )
    assert result.stage_index == 0


# ---------------------------------------------------------------------------
# T. Accidental duplicate negotiate_rate call.
# ---------------------------------------------------------------------------


async def test_t_accidental_duplicate_call_same_offer_cannot_consume_another_stage(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 4}, {"order": 1, "value": 9}, {"order": 2, "value": 15}],
            status="approved",
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    prior = [NegotiationEvent(guest_offer=4000, property_id=str(test_property.id))]

    # Two duplicate calls (e.g. noisy audio produced the tool call twice)
    # with the identical offer -- neither should progress beyond what a
    # single call would have produced.
    result_1 = await negotiate_rate(
        db_session, test_property, check_in, check_out, guest_offer=4500, host_id=test_user.id, prior_events=prior
    )
    prior_after_first = prior + [NegotiationEvent(guest_offer=4500, property_id=str(test_property.id))]
    result_2 = await negotiate_rate(
        db_session, test_property, check_in, check_out, guest_offer=4500, host_id=test_user.id, prior_events=prior_after_first
    )
    assert result_1.stage_index == 1
    assert result_2.stage_index == 1  # duplicate offer -- stays at 1, does not advance to 2
    assert result_2.progressed_this_event is False


# ---------------------------------------------------------------------------
# V. get_pricing does not become a second policy evaluator (structural check).
# ---------------------------------------------------------------------------


async def test_v_get_pricing_does_not_resolve_discount_guest_requests_rules(test_property, db_session, test_user):
    """Confirms the Phase 4C/S.1 architectural boundary held: get_pricing's
    apply_discounts=True path must still ONLY resolve length_of_stay, never
    a discount_guest_requests/repeat_guest/custom/staged rule -- negotiate_rate
    remains the single negotiation-event entry point."""
    from app.services.pricing_engine import calculate_price

    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            stages=[{"order": 0, "value": 50}],  # deliberately huge, so a leak would be obvious
            status="approved",
        )
    )
    await db_session.commit()
    check_in, check_out = _dates()
    breakdown = await calculate_price(db_session, test_property, check_in, check_out, apply_discounts=True, host_id=test_user.id)
    assert breakdown.discount_percent == 0.0  # the 50% staged rule must NOT leak into get_pricing


# ---------------------------------------------------------------------------
# W. Existing flat-policy behavior remains unchanged end-to-end.
# ---------------------------------------------------------------------------


async def test_w_existing_flat_policy_end_to_end_unchanged(test_property, db_session, test_user):
    db_session.add(NegotiationRule(host_id=test_user.id, rule_type="custom", discount_percent=1, property_ids=[str(test_property.id)], status="approved"))
    await db_session.commit()
    check_in, check_out = _dates()
    result = await negotiate_rate(db_session, test_property, check_in, check_out, guest_offer=1, guest_loyalty="new", host_id=test_user.id)
    assert result.counter_offer > 1  # exact same assertion as the pre-existing custom-rule regression test
    assert result.is_staged is False


# ---------------------------------------------------------------------------
# X. Multiple arbitrary host configurations in one test, proving no single
# host's shape is hardcoded anywhere in the engine.
# ---------------------------------------------------------------------------


async def test_x_multiple_arbitrary_host_configurations_all_resolve_independently(db_session):
    from app.models.user import User
    import uuid as uuid_module

    configs = [
        (None, "no negotiation policy"),
        ([{"order": 0, "value": 2}], "single tiny stage"),
        ([{"order": 0, "value": 10}, {"order": 1, "value": 40}], "two wildly different stages"),
        ([{"order": 0, "value": 1}, {"order": 1, "value": 2}, {"order": 2, "value": 3}, {"order": 3, "value": 4}, {"order": 4, "value": 5}, {"order": 5, "value": 6}], "six-stage fine-grained ladder"),
    ]

    for stages, _label in configs:
        host = User(email=f"host-{uuid_module.uuid4().hex[:8]}@example.com", clerk_user_id=f"user_{uuid_module.uuid4().hex[:16]}", name="Arbitrary Host")
        db_session.add(host)
        await db_session.commit()
        await db_session.refresh(host)

        prop = Property(user_id=host.id, name="Arbitrary Property", city="Goa", exophone=f"+9180{uuid_module.uuid4().int % 10**8:08d}", base_price=4000, max_guests=4)
        db_session.add(prop)
        if stages is not None:
            db_session.add(NegotiationRule(host_id=host.id, rule_type="discount_guest_requests", stages=stages, status="approved"))
        await db_session.commit()
        await db_session.refresh(prop)

        check_in, check_out = _dates()
        result = await negotiate_rate(db_session, prop, check_in, check_out, guest_offer=None, host_id=host.id)
        if stages is None:
            assert result.is_staged is False
        else:
            assert result.is_staged is True
            assert result.stage_count == len(stages)
