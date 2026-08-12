"""Phase 2: resolve_effective_call_owner unit tests. Pure function, no DB --
these deliberately do NOT use the db_session/test_property fixtures (which
require a real Postgres round-trip); a plain in-memory Property() ORM
object, never added to a session, is enough to exercise the resolver's
logic. Every datetime is an explicit, fixed, timezone-aware literal --
never datetime.now()."""

from datetime import datetime, timezone

import pytest

from app.models.property import Property
from app.services.call_ownership import CallOwner, InvalidCallOwnershipConfigError, resolve_effective_call_owner


def _property(**overrides) -> Property:
    defaults = dict(
        name="Test Villa",
        base_price=1000,
        call_handling_mode="MIRA",
        call_handling_schedule_start=None,
        call_handling_schedule_end=None,
        timezone="Asia/Kolkata",
    )
    defaults.update(overrides)
    return Property(**defaults)


# 1. MIRA mode -------------------------------------------------------------


def test_mira_mode_always_returns_mira():
    prop = _property(call_handling_mode="MIRA")
    at_any_time = datetime(2026, 8, 11, 3, 0, tzinfo=timezone.utc)
    assert resolve_effective_call_owner(prop, at_any_time) == CallOwner.MIRA


def test_mira_mode_ignores_a_populated_but_irrelevant_schedule():
    """MIRA is unconditional -- schedule fields are never consulted even if
    a host previously configured one and then switched modes back."""
    prop = _property(
        call_handling_mode="MIRA", call_handling_schedule_start="09:00", call_handling_schedule_end="17:00"
    )
    at_noon_ist = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)  # 12:00 IST
    assert resolve_effective_call_owner(prop, at_noon_ist) == CallOwner.MIRA


# 2. HOST mode ---------------------------------------------------------------


def test_host_mode_always_returns_host():
    prop = _property(call_handling_mode="HOST")
    at_any_time = datetime(2026, 8, 11, 3, 0, tzinfo=timezone.utc)
    assert resolve_effective_call_owner(prop, at_any_time) == CallOwner.HOST


def test_host_mode_ignores_a_populated_but_irrelevant_schedule():
    prop = _property(
        call_handling_mode="HOST", call_handling_schedule_start="09:00", call_handling_schedule_end="17:00"
    )
    at_3am_ist = datetime(2026, 8, 11, 21, 30, tzinfo=timezone.utc)  # 03:00 IST next day
    assert resolve_effective_call_owner(prop, at_3am_ist) == CallOwner.HOST


# 3. SCHEDULED normal (same-day) hours --------------------------------------


def test_scheduled_normal_hours_outside_window_is_mira():
    """11:00-17:00 host hours; 20:00 local -> outside window -> MIRA."""
    prop = _property(
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="11:00",
        call_handling_schedule_end="17:00",
        timezone="Asia/Kolkata",
    )
    at_8pm_ist = datetime(2026, 8, 11, 14, 30, tzinfo=timezone.utc)  # 20:00 IST
    assert resolve_effective_call_owner(prop, at_8pm_ist) == CallOwner.MIRA


def test_scheduled_normal_hours_inside_window_is_host():
    prop = _property(
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="11:00",
        call_handling_schedule_end="17:00",
        timezone="Asia/Kolkata",
    )
    at_noon_ist = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)  # 12:00 IST
    assert resolve_effective_call_owner(prop, at_noon_ist) == CallOwner.HOST


# 4-7. Exact boundaries -------------------------------------------------------


def test_scheduled_exact_start_is_host():
    """[start, end) -- start itself is inclusive, HOST."""
    prop = _property(
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="11:00",
        call_handling_schedule_end="17:00",
        timezone="Asia/Kolkata",
    )
    at_exactly_11am_ist = datetime(2026, 8, 11, 5, 30, tzinfo=timezone.utc)  # 11:00:00 IST
    assert resolve_effective_call_owner(prop, at_exactly_11am_ist) == CallOwner.HOST


def test_scheduled_just_before_start_is_mira():
    prop = _property(
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="11:00",
        call_handling_schedule_end="17:00",
        timezone="Asia/Kolkata",
    )
    at_1059_59_ist = datetime(2026, 8, 11, 5, 29, 59, tzinfo=timezone.utc)  # 10:59:59 IST
    assert resolve_effective_call_owner(prop, at_1059_59_ist) == CallOwner.MIRA


def test_scheduled_exact_end_is_mira():
    """[start, end) -- end itself is exclusive, MIRA."""
    prop = _property(
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="11:00",
        call_handling_schedule_end="17:00",
        timezone="Asia/Kolkata",
    )
    at_exactly_5pm_ist = datetime(2026, 8, 11, 11, 30, tzinfo=timezone.utc)  # 17:00:00 IST
    assert resolve_effective_call_owner(prop, at_exactly_5pm_ist) == CallOwner.MIRA


def test_scheduled_just_before_end_is_host():
    prop = _property(
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="11:00",
        call_handling_schedule_end="17:00",
        timezone="Asia/Kolkata",
    )
    at_1659_59_ist = datetime(2026, 8, 11, 11, 29, 59, tzinfo=timezone.utc)  # 16:59:59 IST
    assert resolve_effective_call_owner(prop, at_1659_59_ist) == CallOwner.HOST


# 8-9. Overnight schedule + midnight ------------------------------------------


@pytest.mark.parametrize(
    "utc_dt, expected",
    [
        # 22:00 IST = 16:30 UTC (same calendar day); 06:00 IST = 00:30 UTC (next day)
        (datetime(2026, 8, 11, 16, 29, 59, tzinfo=timezone.utc), CallOwner.MIRA),  # 21:59:59 IST
        (datetime(2026, 8, 11, 16, 30, 0, tzinfo=timezone.utc), CallOwner.HOST),  # 22:00:00 IST
        (datetime(2026, 8, 11, 18, 29, 59, tzinfo=timezone.utc), CallOwner.HOST),  # 23:59:59 IST
        (datetime(2026, 8, 11, 18, 30, 0, tzinfo=timezone.utc), CallOwner.HOST),  # 00:00:00 IST (midnight, next day)
        (datetime(2026, 8, 12, 0, 29, 59, tzinfo=timezone.utc), CallOwner.HOST),  # 05:59:59 IST
        (datetime(2026, 8, 12, 0, 30, 0, tzinfo=timezone.utc), CallOwner.MIRA),  # 06:00:00 IST
    ],
)
def test_scheduled_overnight_window(utc_dt, expected):
    """22:00 -> 06:00 host hours, exactly the boundary table from the spec."""
    prop = _property(
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="22:00",
        call_handling_schedule_end="06:00",
        timezone="Asia/Kolkata",
    )
    assert resolve_effective_call_owner(prop, utc_dt) == expected


def test_scheduled_overnight_window_at_local_midnight_is_host():
    """Local midnight sits strictly inside a 22:00->06:00 window -- HOST,
    distinct from the exact-start/exact-end boundary tests above."""
    prop = _property(
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="22:00",
        call_handling_schedule_end="06:00",
        timezone="Asia/Kolkata",
    )
    local_midnight_utc = datetime(2026, 8, 11, 18, 30, 0, tzinfo=timezone.utc)  # 00:00:00 IST
    assert resolve_effective_call_owner(prop, local_midnight_utc) == CallOwner.HOST


# 10. UTC/local date boundary --------------------------------------------------


def test_scheduled_crosses_utc_calendar_day_boundary_correctly():
    """A local-time window that straddles the UTC date rollover must still
    resolve correctly purely from wall-clock comparison -- no date
    arithmetic. 23:30 IST on Aug 11 is 18:00 UTC on Aug 11; 00:30 IST on
    Aug 12 (still well within a same-day 22:00-06:00 window) is 19:00 UTC
    on Aug 11 -- the UTC calendar day hasn't even rolled over yet, while
    the IST calendar day has. Confirms the resolver never conflates "UTC
    day" with "property-local day"."""
    prop = _property(
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="22:00",
        call_handling_schedule_end="06:00",
        timezone="Asia/Kolkata",
    )
    still_aug_11_utc_but_past_midnight_ist = datetime(2026, 8, 11, 19, 0, 0, tzinfo=timezone.utc)  # 00:30 IST Aug 12
    assert resolve_effective_call_owner(prop, still_aug_11_utc_but_past_midnight_ist) == CallOwner.HOST


# 11. Asia/Kolkata (already exercised throughout, one explicit smoke test) ----


def test_asia_kolkata_offset_is_plus_5_30_not_a_whole_hour():
    """IST's +5:30 offset (not a whole-hour offset like most zones) is
    exactly why naive +N-hour arithmetic is unsafe -- this confirms
    ZoneInfo, not manual arithmetic, is what's actually driving the
    conversion."""
    prop = _property(
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="11:00",
        call_handling_schedule_end="17:00",
        timezone="Asia/Kolkata",
    )
    at_11_00_ist_exactly = datetime(2026, 8, 11, 5, 30, 0, tzinfo=timezone.utc)
    assert resolve_effective_call_owner(prop, at_11_00_ist_exactly) == CallOwner.HOST


# 12. A non-India timezone ------------------------------------------------------


def test_non_india_timezone_america_new_york():
    """Confirms the resolver is not hard-coded to India in any way --
    same 11:00-17:00 host hours, evaluated in America/New_York instead."""
    prop = _property(
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="11:00",
        call_handling_schedule_end="17:00",
        timezone="America/New_York",
    )
    # 2026-08-11 is in EDT (UTC-4): 11:00 EDT = 15:00 UTC.
    at_11am_edt = datetime(2026, 8, 11, 15, 0, 0, tzinfo=timezone.utc)
    assert resolve_effective_call_owner(prop, at_11am_edt) == CallOwner.HOST

    at_1059_edt = datetime(2026, 8, 11, 14, 59, 59, tzinfo=timezone.utc)
    assert resolve_effective_call_owner(prop, at_1059_edt) == CallOwner.MIRA


# 13. DST behavior ---------------------------------------------------------------


def test_dst_transition_is_handled_correctly_by_zoneinfo():
    """America/New_York: 2026-03-08 02:00 EST springs forward to 03:00 EDT
    (a real DST transition date/time for 2026). A property with host hours
    11:00-17:00 must resolve identically in wall-clock terms on both sides
    of the transition, proving ZoneInfo (not a fixed offset) drives the
    comparison -- a fixed-offset implementation would silently drift by an
    hour across this date."""
    prop = _property(
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="11:00",
        call_handling_schedule_end="17:00",
        timezone="America/New_York",
    )
    # Before the 2026 spring-forward: 11:00 EST = 16:00 UTC.
    before_dst_11am_est = datetime(2026, 3, 1, 16, 0, 0, tzinfo=timezone.utc)
    assert resolve_effective_call_owner(prop, before_dst_11am_est) == CallOwner.HOST

    # After the 2026 spring-forward: 11:00 EDT = 15:00 UTC (one hour earlier
    # in UTC terms for the same 11:00 local wall-clock reading).
    after_dst_11am_edt = datetime(2026, 3, 15, 15, 0, 0, tzinfo=timezone.utc)
    assert resolve_effective_call_owner(prop, after_dst_11am_edt) == CallOwner.HOST


# 14. Invalid timezone --------------------------------------------------------------


def test_invalid_timezone_raises_instead_of_silently_falling_back():
    prop = _property(
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="09:00",
        call_handling_schedule_end="17:00",
        timezone="Not/A_Real_Zone",
    )
    at_any_time = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    with pytest.raises(InvalidCallOwnershipConfigError, match="not a valid IANA timezone"):
        resolve_effective_call_owner(prop, at_any_time)


def test_missing_timezone_raises():
    prop = _property(
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="09:00",
        call_handling_schedule_end="17:00",
        timezone=None,
    )
    at_any_time = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    with pytest.raises(InvalidCallOwnershipConfigError, match="requires a property timezone"):
        resolve_effective_call_owner(prop, at_any_time)


# 15. Missing schedule fields --------------------------------------------------------


def test_scheduled_missing_start_raises():
    prop = _property(
        call_handling_mode="SCHEDULED", call_handling_schedule_start=None, call_handling_schedule_end="17:00"
    )
    at_any_time = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    with pytest.raises(InvalidCallOwnershipConfigError, match="requires both"):
        resolve_effective_call_owner(prop, at_any_time)


def test_scheduled_missing_end_raises():
    prop = _property(
        call_handling_mode="SCHEDULED", call_handling_schedule_start="09:00", call_handling_schedule_end=None
    )
    at_any_time = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    with pytest.raises(InvalidCallOwnershipConfigError, match="requires both"):
        resolve_effective_call_owner(prop, at_any_time)


def test_scheduled_missing_both_start_and_end_raises():
    prop = _property(
        call_handling_mode="SCHEDULED", call_handling_schedule_start=None, call_handling_schedule_end=None
    )
    at_any_time = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    with pytest.raises(InvalidCallOwnershipConfigError, match="requires both"):
        resolve_effective_call_owner(prop, at_any_time)


def test_scheduled_malformed_time_string_raises():
    """Defensive: PropertyUpdate's schema validator (Phase 1) already
    blocks this from ever being saved via the API, but the resolver itself
    must not silently misinterpret a bad string if one somehow exists on a
    row (e.g. written directly against the DB)."""
    prop = _property(
        call_handling_mode="SCHEDULED", call_handling_schedule_start="not-a-time", call_handling_schedule_end="17:00"
    )
    at_any_time = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    with pytest.raises(InvalidCallOwnershipConfigError, match="not a valid HH:MM"):
        resolve_effective_call_owner(prop, at_any_time)


def test_scheduled_out_of_range_hour_raises():
    prop = _property(
        call_handling_mode="SCHEDULED", call_handling_schedule_start="25:00", call_handling_schedule_end="17:00"
    )
    at_any_time = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    with pytest.raises(InvalidCallOwnershipConfigError, match="not a valid HH:MM"):
        resolve_effective_call_owner(prop, at_any_time)


def test_parse_hh_mm_raises_domain_error_not_attribute_error_for_none():
    """Regression: _parse_hh_mm(None, ...) previously raised a raw
    AttributeError (from None.split()) instead of the intended
    InvalidCallOwnershipConfigError -- unreachable via
    resolve_effective_call_owner today (its own None-guard runs first),
    but _parse_hh_mm is a free function that must validate its own input
    rather than relying on every future caller to have already checked
    for None."""
    from app.services.call_ownership import _parse_hh_mm

    with pytest.raises(InvalidCallOwnershipConfigError, match="not a valid HH:MM"):
        _parse_hh_mm(None, field_name="call_handling_schedule_start")


# 16. Equal start/end -----------------------------------------------------------------


def test_equal_start_and_end_is_always_mira():
    """start == end is an empty [start, end) interval -- host hours never
    occur, so every call resolves to MIRA regardless of time of day. See
    _time_in_half_open_interval's own docstring for the full reasoning."""
    prop = _property(
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="09:00",
        call_handling_schedule_end="09:00",
        timezone="Asia/Kolkata",
    )
    at_exactly_9am_ist = datetime(2026, 8, 11, 3, 30, tzinfo=timezone.utc)
    at_midnight_ist = datetime(2026, 8, 11, 18, 30, tzinfo=timezone.utc)
    at_end_of_day_ist = datetime(2026, 8, 12, 18, 29, 59, tzinfo=timezone.utc)

    assert resolve_effective_call_owner(prop, at_exactly_9am_ist) == CallOwner.MIRA
    assert resolve_effective_call_owner(prop, at_midnight_ist) == CallOwner.MIRA
    assert resolve_effective_call_owner(prop, at_end_of_day_ist) == CallOwner.MIRA


# Extra: unknown/invalid mode string, naive datetime rejection ------------------------


def test_unknown_call_handling_mode_raises():
    prop = _property(call_handling_mode="SOMETHING_ELSE")
    at_any_time = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    with pytest.raises(InvalidCallOwnershipConfigError, match="not one of MIRA, HOST, SCHEDULED"):
        resolve_effective_call_owner(prop, at_any_time)


def test_naive_datetime_raises_instead_of_being_assumed_utc():
    prop = _property(call_handling_mode="MIRA")
    naive_dt = datetime(2026, 8, 11, 6, 30)  # no tzinfo
    with pytest.raises(InvalidCallOwnershipConfigError, match="timezone-aware"):
        resolve_effective_call_owner(prop, naive_dt)


def test_same_input_produces_same_output_repeatedly():
    """Purity smoke test -- no hidden state, no memoization surprises."""
    prop = _property(
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="11:00",
        call_handling_schedule_end="17:00",
        timezone="Asia/Kolkata",
    )
    at_noon_ist = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    results = {resolve_effective_call_owner(prop, at_noon_ist) for _ in range(5)}
    assert results == {CallOwner.HOST}
