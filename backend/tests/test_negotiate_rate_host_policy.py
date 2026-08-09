"""Covers negotiate_rate's Host Memory wiring (memory-architecture-plan.md
section 4.4/4.6) -- approved discount_* NegotiationRule rows changing
negotiation behavior, and the mandatory fallback to today's exact
pre-existing global-constant behavior when no host policy exists or the
lookup fails."""

from datetime import date, timedelta

from app.models.negotiation_rule import NegotiationRule
from app.services.pricing_engine import MAX_NEGOTIATION_DISCOUNT_PERCENT, negotiate_rate


def _next_weekday(start: date, weekday: int) -> date:
    delta = (weekday - start.weekday()) % 7
    return start + timedelta(days=delta)


async def test_no_host_id_falls_back_to_global_defaults(test_property, db_session):
    """host_id=None (the default, and what every call site used before this
    change) must behave byte-for-byte like the pre-Host-Memory code."""
    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)

    result = await negotiate_rate(db_session, test_property, monday, wednesday, guest_offer=1, guest_loyalty="new")
    breakdown_asking = result.asking_price
    # loyalty_bonus(new)=0 + 10 = 10%, capped at MAX_NEGOTIATION_DISCOUNT_PERCENT(15) -> 10%
    expected_floor = round(breakdown_asking * (1 - 10 / 100), 2)
    assert result.counter_offer == expected_floor


async def test_host_with_no_approved_rules_falls_back_to_global_defaults(test_property, test_user, db_session):
    """A host_id that exists but has zero approved discount_* NegotiationRule
    rows (the common case right after Phase 1 shipped, before any host has
    used AI Training yet) must also fall back exactly as before -- this is
    the single most important behavior-preservation guarantee in this whole
    change, since every existing host is in this state today."""
    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)

    result = await negotiate_rate(
        db_session, test_property, monday, wednesday, guest_offer=1, guest_loyalty="new", host_id=test_user.id
    )
    expected_floor = round(result.asking_price * (1 - 10 / 100), 2)
    assert result.counter_offer == expected_floor
    assert result.refused is False


async def test_pending_validation_rule_is_not_used(test_property, test_user, db_session):
    """A rule that hasn't been approved yet must have zero effect on live
    negotiation -- this is the core safety guarantee of the whole Host
    Memory validation flow."""
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            discount_percent=50,  # deliberately extreme so a leak would be obvious
            status="pending_validation",
        )
    )
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)
    result = await negotiate_rate(
        db_session, test_property, monday, wednesday, guest_offer=1, guest_loyalty="new", host_id=test_user.id
    )
    expected_floor = round(result.asking_price * (1 - 10 / 100), 2)
    assert result.counter_offer == expected_floor  # not the 50% pending rule


async def test_approved_guest_requests_rule_sets_discount_ceiling(test_property, test_user, db_session):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            discount_percent=5,
            status="approved",
        )
    )
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)
    result = await negotiate_rate(
        db_session, test_property, monday, wednesday, guest_offer=1, guest_loyalty="new", host_id=test_user.id
    )
    expected_floor = round(result.asking_price * (1 - 5 / 100), 2)
    assert result.counter_offer == expected_floor


async def test_approved_repeat_guest_rule_applies_only_for_returning_loyalty(test_property, test_user, db_session):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_repeat_guest",
            discount_percent=8,
            status="approved",
        )
    )
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            discount_percent=5,
            status="approved",
        )
    )
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)

    returning_result = await negotiate_rate(
        db_session, test_property, monday, wednesday, guest_offer=1, guest_loyalty="returning", host_id=test_user.id
    )
    expected_floor_returning = round(returning_result.asking_price * (1 - 8 / 100), 2)
    assert returning_result.counter_offer == expected_floor_returning

    new_guest_result = await negotiate_rate(
        db_session, test_property, monday, wednesday, guest_offer=1, guest_loyalty="new", host_id=test_user.id
    )
    expected_floor_new = round(new_guest_result.asking_price * (1 - 5 / 100), 2)
    assert new_guest_result.counter_offer == expected_floor_new


async def test_negotiation_allowed_false_refuses_without_erroring(test_property, test_user, db_session):
    test_user.negotiation_allowed = False
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)
    result = await negotiate_rate(
        db_session, test_property, monday, wednesday, guest_offer=1, guest_loyalty="new", host_id=test_user.id
    )
    assert result.refused is True
    assert result.accepted is False
    assert result.counter_offer == result.asking_price  # no discount at all
    assert "best price" in result.message.lower()


async def test_max_discount_percent_override_caps_negotiation(test_property, test_user, db_session):
    test_user.max_discount_percent_override = 3
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="discount_guest_requests",
            discount_percent=20,  # would exceed the host's own override cap
            status="approved",
        )
    )
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)
    result = await negotiate_rate(
        db_session, test_property, monday, wednesday, guest_offer=1, guest_loyalty="new", host_id=test_user.id
    )
    expected_floor = round(result.asking_price * (1 - 3 / 100), 2)  # capped at 3%, not 20%
    assert result.counter_offer == expected_floor


async def test_lookup_failure_falls_back_to_global_defaults_never_errors(test_property, test_user, db_session, monkeypatch):
    """Simulates the NegotiationRule DB query itself failing (e.g. a
    transient error) inside _get_host_negotiation_policy's own try/except --
    negotiate_rate must still complete using global defaults, never raise,
    per the mandatory fallback rule in section 0.1."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(db_session, "get", _boom)

    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)

    result = await negotiate_rate(
        db_session, test_property, monday, wednesday, guest_offer=1, guest_loyalty="new", host_id=test_user.id
    )
    # Must complete successfully with the untouched global-default behavior,
    # not raise and not hang -- the guest is still live on the call.
    expected_floor = round(result.asking_price * (1 - 10 / 100), 2)
    assert result.counter_offer == expected_floor
    assert result.refused is False
