"""resolve_effective_call_owner unit tests. Pure function, no DB -- these
deliberately do NOT use the db_session/test_property fixtures (which require
a real Postgres round-trip); plain in-memory Property()/User() ORM objects,
never added to a session, are enough to exercise the resolver's logic. Every
datetime is an explicit, fixed, timezone-aware literal -- never
datetime.now().

Routing input as of documentation/host-call-hours-and-handoff.md: the
account-global window on User (host_call_hours_enabled/_start/_end/
_timezone). The per-property Property.call_handling_* columns are no longer
read by the resolver.
"""

from datetime import datetime, timezone

import pytest

from app.config import settings
from app.models.property import Property
from app.models.user import User
from app.services.call_ownership import CallOwner, InvalidCallOwnershipConfigError, resolve_effective_call_owner


def _property(**overrides) -> Property:
    defaults = dict(name="Test Villa", base_price=1000)
    defaults.update(overrides)
    return Property(**defaults)


def _host(**overrides) -> User:
    defaults = dict(
        email="host@example.com",
        host_call_hours_enabled=False,
        host_call_hours_start=None,
        host_call_hours_end=None,
        host_call_hours_timezone="Asia/Kolkata",
    )
    defaults.update(overrides)
    return User(**defaults)


def _resolve(host: User, dt: datetime) -> CallOwner:
    return resolve_effective_call_owner(_property(), host, dt)


# 1. Window disabled -- Mira answers 24/7 -----------------------------------


def test_disabled_window_always_returns_mira():
    host = _host(host_call_hours_enabled=False)
    at_any_time = datetime(2026, 8, 11, 3, 0, tzinfo=timezone.utc)
    assert _resolve(host, at_any_time) == CallOwner.MIRA


def test_disabled_window_ignores_populated_but_irrelevant_bounds():
    """Disabled is unconditional -- start/end are never consulted even if a
    host previously configured a window and then switched it off."""
    host = _host(
        host_call_hours_enabled=False, host_call_hours_start="09:00", host_call_hours_end="17:00"
    )
    at_noon_ist = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)  # 12:00 IST
    assert _resolve(host, at_noon_ist) == CallOwner.MIRA


def test_per_property_call_handling_columns_are_not_consulted():
    """A property carrying a legacy call_handling_mode='HOST' must NOT
    route to the host -- those columns are retired as a routing input."""
    prop = _property(call_handling_mode="HOST", timezone="Asia/Kolkata")
    host = _host(host_call_hours_enabled=False)
    at_any_time = datetime(2026, 8, 11, 3, 0, tzinfo=timezone.utc)
    assert resolve_effective_call_owner(prop, host, at_any_time) == CallOwner.MIRA


# 2. Enabled window, normal (same-day) hours -------------------------------


def test_enabled_window_outside_hours_is_mira():
    """11:00-17:00 host hours; 20:00 local -> outside window -> MIRA."""
    host = _host(
        host_call_hours_enabled=True,
        host_call_hours_start="11:00",
        host_call_hours_end="17:00",
        host_call_hours_timezone="Asia/Kolkata",
    )
    at_8pm_ist = datetime(2026, 8, 11, 14, 30, tzinfo=timezone.utc)  # 20:00 IST
    assert _resolve(host, at_8pm_ist) == CallOwner.MIRA


def test_enabled_window_inside_hours_is_host():
    host = _host(
        host_call_hours_enabled=True,
        host_call_hours_start="11:00",
        host_call_hours_end="17:00",
        host_call_hours_timezone="Asia/Kolkata",
    )
    at_noon_ist = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)  # 12:00 IST
    assert _resolve(host, at_noon_ist) == CallOwner.HOST


# 3. Exact boundaries -- [start, end) --------------------------------------


def _window_11_17() -> User:
    return _host(
        host_call_hours_enabled=True,
        host_call_hours_start="11:00",
        host_call_hours_end="17:00",
        host_call_hours_timezone="Asia/Kolkata",
    )


def test_exact_start_is_host():
    at_exactly_11am_ist = datetime(2026, 8, 11, 5, 30, tzinfo=timezone.utc)  # 11:00:00 IST
    assert _resolve(_window_11_17(), at_exactly_11am_ist) == CallOwner.HOST


def test_just_before_start_is_mira():
    at_1059_59_ist = datetime(2026, 8, 11, 5, 29, 59, tzinfo=timezone.utc)  # 10:59:59 IST
    assert _resolve(_window_11_17(), at_1059_59_ist) == CallOwner.MIRA


def test_exact_end_is_mira():
    at_exactly_5pm_ist = datetime(2026, 8, 11, 11, 30, tzinfo=timezone.utc)  # 17:00:00 IST
    assert _resolve(_window_11_17(), at_exactly_5pm_ist) == CallOwner.MIRA


def test_just_before_end_is_host():
    at_1659_59_ist = datetime(2026, 8, 11, 11, 29, 59, tzinfo=timezone.utc)  # 16:59:59 IST
    assert _resolve(_window_11_17(), at_1659_59_ist) == CallOwner.HOST


# 4. Overnight window + midnight -----------------------------------------------


def _window_22_06() -> User:
    return _host(
        host_call_hours_enabled=True,
        host_call_hours_start="22:00",
        host_call_hours_end="06:00",
        host_call_hours_timezone="Asia/Kolkata",
    )


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
def test_overnight_window(utc_dt, expected):
    """22:00 -> 06:00 host hours, exactly the boundary table."""
    assert _resolve(_window_22_06(), utc_dt) == expected


def test_overnight_window_at_local_midnight_is_host():
    """Local midnight sits strictly inside a 22:00->06:00 window -- HOST,
    distinct from the exact-start/exact-end boundary tests above."""
    local_midnight_utc = datetime(2026, 8, 11, 18, 30, 0, tzinfo=timezone.utc)  # 00:00:00 IST
    assert _resolve(_window_22_06(), local_midnight_utc) == CallOwner.HOST


# 5. UTC/local date boundary -------------------------------------------------


def test_crosses_utc_calendar_day_boundary_correctly():
    """A local-time window that straddles the UTC date rollover must still
    resolve correctly purely from wall-clock comparison -- no date
    arithmetic. 00:30 IST on Aug 12 (still well within a 22:00-06:00
    window) is 19:00 UTC on Aug 11 -- the UTC calendar day hasn't rolled
    over yet, while the IST calendar day has."""
    still_aug_11_utc_but_past_midnight_ist = datetime(2026, 8, 11, 19, 0, 0, tzinfo=timezone.utc)  # 00:30 IST Aug 12
    assert _resolve(_window_22_06(), still_aug_11_utc_but_past_midnight_ist) == CallOwner.HOST


# 6. Asia/Kolkata's +5:30 offset -------------------------------------------


def test_asia_kolkata_offset_is_plus_5_30_not_a_whole_hour():
    """IST's +5:30 offset (not a whole-hour offset like most zones) is
    exactly why naive +N-hour arithmetic is unsafe -- this confirms
    ZoneInfo, not manual arithmetic, drives the conversion."""
    at_11_00_ist_exactly = datetime(2026, 8, 11, 5, 30, 0, tzinfo=timezone.utc)
    assert _resolve(_window_11_17(), at_11_00_ist_exactly) == CallOwner.HOST


# 7. A non-India timezone --------------------------------------------------


def test_non_india_timezone_america_new_york():
    """Confirms the resolver is not hard-coded to India -- same 11:00-17:00
    host hours, evaluated in America/New_York instead."""
    host = _host(
        host_call_hours_enabled=True,
        host_call_hours_start="11:00",
        host_call_hours_end="17:00",
        host_call_hours_timezone="America/New_York",
    )
    # 2026-08-11 is in EDT (UTC-4): 11:00 EDT = 15:00 UTC.
    at_11am_edt = datetime(2026, 8, 11, 15, 0, 0, tzinfo=timezone.utc)
    assert _resolve(host, at_11am_edt) == CallOwner.HOST

    at_1059_edt = datetime(2026, 8, 11, 14, 59, 59, tzinfo=timezone.utc)
    assert _resolve(host, at_1059_edt) == CallOwner.MIRA


# 8. DST behavior --------------------------------------------------------------


def test_dst_transition_is_handled_correctly_by_zoneinfo():
    """America/New_York: a property with host hours 11:00-17:00 must
    resolve identically in wall-clock terms on both sides of the spring-
    forward transition, proving ZoneInfo (not a fixed offset) drives the
    comparison."""
    host = _host(
        host_call_hours_enabled=True,
        host_call_hours_start="11:00",
        host_call_hours_end="17:00",
        host_call_hours_timezone="America/New_York",
    )
    # Before the 2026 spring-forward: 11:00 EST = 16:00 UTC.
    before_dst_11am_est = datetime(2026, 3, 1, 16, 0, 0, tzinfo=timezone.utc)
    assert _resolve(host, before_dst_11am_est) == CallOwner.HOST

    # After the 2026 spring-forward: 11:00 EDT = 15:00 UTC.
    after_dst_11am_edt = datetime(2026, 3, 15, 15, 0, 0, tzinfo=timezone.utc)
    assert _resolve(host, after_dst_11am_edt) == CallOwner.HOST


# 9. Invalid / missing config -- raises, never silently falls back ------------


def test_invalid_timezone_raises_instead_of_silently_falling_back():
    host = _host(
        host_call_hours_enabled=True,
        host_call_hours_start="09:00",
        host_call_hours_end="17:00",
        host_call_hours_timezone="Not/A_Real_Zone",
    )
    at_any_time = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    with pytest.raises(InvalidCallOwnershipConfigError, match="not a valid IANA timezone"):
        _resolve(host, at_any_time)


def test_missing_timezone_raises():
    host = _host(
        host_call_hours_enabled=True,
        host_call_hours_start="09:00",
        host_call_hours_end="17:00",
        host_call_hours_timezone=None,
    )
    at_any_time = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    with pytest.raises(InvalidCallOwnershipConfigError, match="timezone is required"):
        _resolve(host, at_any_time)


def test_enabled_missing_start_raises():
    host = _host(host_call_hours_enabled=True, host_call_hours_start=None, host_call_hours_end="17:00")
    at_any_time = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    with pytest.raises(InvalidCallOwnershipConfigError, match="both start and end are required"):
        _resolve(host, at_any_time)


def test_enabled_missing_end_raises():
    host = _host(host_call_hours_enabled=True, host_call_hours_start="09:00", host_call_hours_end=None)
    at_any_time = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    with pytest.raises(InvalidCallOwnershipConfigError, match="both start and end are required"):
        _resolve(host, at_any_time)


def test_enabled_missing_both_raises():
    host = _host(host_call_hours_enabled=True, host_call_hours_start=None, host_call_hours_end=None)
    at_any_time = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    with pytest.raises(InvalidCallOwnershipConfigError, match="both start and end are required"):
        _resolve(host, at_any_time)


def test_malformed_time_string_raises():
    """Defensive: UserUpdate's schema validator already blocks this from
    being saved via the API, but the resolver itself must not silently
    misinterpret a bad string if one somehow exists on a row."""
    host = _host(
        host_call_hours_enabled=True, host_call_hours_start="not-a-time", host_call_hours_end="17:00"
    )
    at_any_time = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    with pytest.raises(InvalidCallOwnershipConfigError, match="not a valid HH:MM"):
        _resolve(host, at_any_time)


def test_out_of_range_hour_raises():
    host = _host(host_call_hours_enabled=True, host_call_hours_start="25:00", host_call_hours_end="17:00")
    at_any_time = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    with pytest.raises(InvalidCallOwnershipConfigError, match="not a valid HH:MM"):
        _resolve(host, at_any_time)


def test_parse_hh_mm_raises_domain_error_not_attribute_error_for_none():
    """Regression: _parse_hh_mm(None, ...) must raise
    InvalidCallOwnershipConfigError, not a raw AttributeError from
    None.split()."""
    from app.services.call_ownership import _parse_hh_mm

    with pytest.raises(InvalidCallOwnershipConfigError, match="not a valid HH:MM"):
        _parse_hh_mm(None, field_name="host_call_hours_start")


def test_naive_datetime_raises_instead_of_being_assumed_utc():
    host = _host(host_call_hours_enabled=False)
    naive_dt = datetime(2026, 8, 11, 6, 30)  # no tzinfo
    with pytest.raises(InvalidCallOwnershipConfigError, match="timezone-aware"):
        _resolve(host, naive_dt)


# 10. Equal start/end -- empty interval, always MIRA -------------------------


def test_equal_start_and_end_is_always_mira():
    """start == end is an empty [start, end) interval -- host hours never
    occur, so every call resolves to MIRA regardless of time of day."""
    host = _host(
        host_call_hours_enabled=True,
        host_call_hours_start="09:00",
        host_call_hours_end="09:00",
        host_call_hours_timezone="Asia/Kolkata",
    )
    at_exactly_9am_ist = datetime(2026, 8, 11, 3, 30, tzinfo=timezone.utc)
    at_midnight_ist = datetime(2026, 8, 11, 18, 30, tzinfo=timezone.utc)
    at_end_of_day_ist = datetime(2026, 8, 12, 18, 29, 59, tzinfo=timezone.utc)

    assert _resolve(host, at_exactly_9am_ist) == CallOwner.MIRA
    assert _resolve(host, at_midnight_ist) == CallOwner.MIRA
    assert _resolve(host, at_end_of_day_ist) == CallOwner.MIRA


# 11. TEMPORARY fixed_host_hours_start/_end override -----------------------
# See Settings.fixed_host_hours_start/_end's own comment in config.py and
# the override block at the top of resolve_effective_call_owner. When both
# are set, EVERY host is forced onto one hardcoded Asia/Kolkata HOST window
# regardless of their own host_call_hours_* config -- these tests use
# disabled-window hosts specifically to prove the override actually bypasses
# per-account config, not just happens to agree with it.


@pytest.fixture
def fixed_host_hours_11_to_17(monkeypatch):
    monkeypatch.setattr(settings, "fixed_host_hours_start", "11:00")
    monkeypatch.setattr(settings, "fixed_host_hours_end", "17:00")


def test_fixed_hours_override_ignores_disabled_window_during_host_window(fixed_host_hours_11_to_17):
    host = _host(host_call_hours_enabled=False)
    at_noon_ist = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)  # 12:00 IST
    assert _resolve(host, at_noon_ist) == CallOwner.HOST


def test_fixed_hours_override_ignores_enabled_window_outside_host_window(fixed_host_hours_11_to_17):
    """Host's own window says 00:00-23:59 (always HOST); the override still
    forces MIRA at 20:00 IST because 20:00 is outside the fixed 11-17."""
    host = _host(
        host_call_hours_enabled=True,
        host_call_hours_start="00:00",
        host_call_hours_end="23:59",
        host_call_hours_timezone="Asia/Kolkata",
    )
    at_8pm_ist = datetime(2026, 8, 11, 14, 30, tzinfo=timezone.utc)  # 20:00 IST
    assert _resolve(host, at_8pm_ist) == CallOwner.MIRA


def test_fixed_hours_override_ignores_host_timezone(fixed_host_hours_11_to_17):
    """The override always evaluates in Asia/Kolkata, never the host's own
    timezone. Noon IST is 06:30 UTC; in America/New_York (EDT) that's 02:30
    local -- if the override wrongly consulted the host timezone this would
    resolve MIRA, not HOST."""
    host = _host(host_call_hours_enabled=True, host_call_hours_timezone="America/New_York")
    at_noon_ist = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    assert _resolve(host, at_noon_ist) == CallOwner.HOST


def test_fixed_hours_override_at_exact_boundaries(fixed_host_hours_11_to_17):
    host = _host(host_call_hours_enabled=False)
    at_exactly_11am_ist = datetime(2026, 8, 11, 5, 30, tzinfo=timezone.utc)
    at_exactly_5pm_ist = datetime(2026, 8, 11, 11, 30, tzinfo=timezone.utc)
    assert _resolve(host, at_exactly_11am_ist) == CallOwner.HOST
    assert _resolve(host, at_exactly_5pm_ist) == CallOwner.MIRA


def test_fixed_hours_override_unset_falls_back_to_host_config():
    """Both settings unset (the default/production-today state) -- resolver
    reads the host's own window."""
    assert settings.fixed_host_hours_start is None
    assert settings.fixed_host_hours_end is None
    host = _host(
        host_call_hours_enabled=True,
        host_call_hours_start="22:00",
        host_call_hours_end="06:00",
        host_call_hours_timezone="Asia/Kolkata",
    )
    at_3am_ist = datetime(2026, 8, 11, 21, 30, tzinfo=timezone.utc)  # 03:00 IST next day
    assert _resolve(host, at_3am_ist) == CallOwner.HOST


def test_same_input_produces_same_output_repeatedly():
    """Purity smoke test -- no hidden state, no memoization surprises."""
    host = _window_11_17()
    at_noon_ist = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)
    results = {_resolve(host, at_noon_ist) for _ in range(5)}
    assert results == {CallOwner.HOST}
