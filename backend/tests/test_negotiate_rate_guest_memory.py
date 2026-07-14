"""Covers negotiate_rate's real Guest Memory wiring (memory-architecture-plan.md
section 1, connecting to section 4.4's repeat_guest_same_host trigger):
GuestProfile.total_stays (now host-scoped) is consulted directly instead of
only the LLM-supplied guest_loyalty argument, with a fallback to
guest_loyalty when no guest profile is resolvable."""

from datetime import date, timedelta

from app.models.guest_profile import GuestProfile
from app.models.host_discount_rule import HostDiscountRule
from app.services.pricing_engine import negotiate_rate


def _next_weekday(start: date, weekday: int) -> date:
    delta = (weekday - start.weekday()) % 7
    return start + timedelta(days=delta)


async def _approved_repeat_guest_rule(db_session, host_id, percent=8):
    db_session.add(
        HostDiscountRule(host_id=host_id, trigger_type="repeat_guest_same_host", discount_percent=percent, status="approved")
    )
    await db_session.commit()


async def test_guest_profile_with_two_plus_stays_triggers_repeat_guest_rule_even_with_loyalty_new(
    test_property, test_user, db_session
):
    """The real signal (GuestProfile.total_stays >= 2 for THIS host) must
    win even if the LLM mistakenly/conservatively passes guest_loyalty="new"
    -- this is the whole point of not trusting the LLM-supplied signal
    alone once real data exists."""
    await _approved_repeat_guest_rule(db_session, test_user.id)
    guest = GuestProfile(phone="+919999999999", host_id=test_user.id, total_stays=3)
    db_session.add(guest)
    await db_session.commit()
    await db_session.refresh(guest)

    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)
    result = await negotiate_rate(
        db_session,
        test_property,
        monday,
        wednesday,
        guest_offer=1,
        guest_loyalty="new",
        host_id=test_user.id,
        guest_profile_id=guest.id,
    )
    expected_floor = round(result.asking_price * (1 - 8 / 100), 2)
    assert result.counter_offer == expected_floor


async def test_guest_profile_with_one_stay_does_not_trigger_repeat_guest_rule(test_property, test_user, db_session):
    """total_stays == 1 means this is their first-ever call with this host
    (the current call incremented it from 0 -- see guest_memory_service.py)
    -- not yet a repeat guest."""
    await _approved_repeat_guest_rule(db_session, test_user.id)
    guest = GuestProfile(phone="+919999999999", host_id=test_user.id, total_stays=1)
    db_session.add(guest)
    await db_session.commit()
    await db_session.refresh(guest)

    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)
    result = await negotiate_rate(
        db_session,
        test_property,
        monday,
        wednesday,
        guest_offer=1,
        guest_loyalty="new",
        host_id=test_user.id,
        guest_profile_id=guest.id,
    )
    # Real signal (not a repeat guest yet) wins -- falls through to default
    # loyalty math, not the 8% repeat-guest rule.
    expected_floor = round(result.asking_price * (1 - 10 / 100), 2)
    assert result.counter_offer == expected_floor


async def test_no_guest_profile_id_falls_back_to_guest_loyalty_argument(test_property, test_user, db_session):
    """No resolvable guest profile at all (e.g. a caller_number that never
    got captured) -- must fall back to the LLM-supplied guest_loyalty
    instead of assuming "not a repeat guest"."""
    await _approved_repeat_guest_rule(db_session, test_user.id)

    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)
    result = await negotiate_rate(
        db_session,
        test_property,
        monday,
        wednesday,
        guest_offer=1,
        guest_loyalty="returning",
        host_id=test_user.id,
        guest_profile_id=None,
    )
    expected_floor = round(result.asking_price * (1 - 8 / 100), 2)
    assert result.counter_offer == expected_floor


async def test_guest_profile_lookup_failure_falls_back_to_guest_loyalty(test_property, test_user, db_session, monkeypatch):
    await _approved_repeat_guest_rule(db_session, test_user.id)
    guest = GuestProfile(phone="+919999999999", host_id=test_user.id, total_stays=5)
    db_session.add(guest)
    await db_session.commit()
    await db_session.refresh(guest)

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(db_session, "get", _boom)

    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)
    result = await negotiate_rate(
        db_session,
        test_property,
        monday,
        wednesday,
        guest_offer=1,
        guest_loyalty="new",
        host_id=test_user.id,
        guest_profile_id=guest.id,
    )
    # Both the host-policy lookup AND the guest-memory lookup use db.get,
    # both monkeypatched to fail -- confirms neither failure cascades into
    # an error, and behavior falls all the way back to plain guest_loyalty
    # math with no host policy applied either.
    expected_floor = round(result.asking_price * (1 - 10 / 100), 2)
    assert result.counter_offer == expected_floor
    assert result.refused is False
