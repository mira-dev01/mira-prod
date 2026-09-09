"""GET /webhooks/exotel/call-routing -- the initial call-ownership Passthru.
Every test uses a fixed clock (monkeypatch on the endpoint module's own
`datetime`, same pattern as test_system_prompt.py) -- datetime.now() is
never called directly in these tests. Fail-closed scenarios (missing
CallSid, unknown DID, invalid config, resolver error) all assert HTTP 200
(MIRA), never 302 -- routing an unresolvable call to a host who never opted
in would be the real incident here.

Routing input as of documentation/host-call-hours-and-handoff.md: the
account-global window on User (host_call_hours_*). These tests configure it
on test_user, not on the property.
"""

import uuid
from datetime import datetime

from app.api.v1.webhooks import exotel
from app.models.property import Property


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 11, 6, 30, tzinfo=tz) if tz else cls(2026, 8, 11, 6, 30)


async def _property_with(db_session, test_user, **overrides) -> Property:
    defaults = dict(
        user_id=test_user.id,
        name="Routing Test Villa",
        base_price=1000,
        exophone=f"+9180{uuid.uuid4().int % 10**8:08d}",
    )
    defaults.update(overrides)
    property_ = Property(**defaults)
    db_session.add(property_)
    await db_session.commit()
    await db_session.refresh(property_)
    return property_


async def _set_host_window(db_session, test_user, **fields) -> None:
    """Configure test_user's account-global host call hours window."""
    for key, value in fields.items():
        setattr(test_user, key, value)
    await db_session.commit()
    await db_session.refresh(test_user)


# 1. Window disabled -- Mira answers 24/7 ------------------------------------


async def test_disabled_window_returns_200(client, db_session, test_user):
    property_ = await _property_with(db_session, test_user)
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_1", "To": property_.exophone, "From": "+919999999999"},
    )
    assert resp.status_code == 200


async def test_legacy_property_call_handling_mode_is_ignored(client, db_session, test_user):
    """A property carrying a legacy call_handling_mode='HOST' must NOT
    produce a 302 -- those columns are no longer a routing input."""
    property_ = await _property_with(db_session, test_user, call_handling_mode="HOST")
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_2", "To": property_.exophone, "From": "+919999999999"},
        follow_redirects=False,
    )
    assert resp.status_code == 200


# 2. Enabled window during host hours --------------------------------------


async def test_enabled_window_during_host_hours_returns_302(client, db_session, test_user, monkeypatch):
    """Fixed clock: 2026-08-11 06:30 UTC = 12:00 IST -- inside an
    11:00-17:00 host-hours window."""
    monkeypatch.setattr(exotel, "datetime", _FixedDatetime)
    await _set_host_window(
        db_session,
        test_user,
        host_call_hours_enabled=True,
        host_call_hours_start="11:00",
        host_call_hours_end="17:00",
        host_call_hours_timezone="Asia/Kolkata",
    )
    property_ = await _property_with(db_session, test_user)
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_3", "To": property_.exophone},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# 3. Enabled window outside host hours ------------------------------------------


async def test_enabled_window_outside_host_hours_returns_200(client, db_session, test_user, monkeypatch):
    """Same fixed clock (12:00 IST) but host hours are 18:00-22:00 --
    outside the window -> MIRA."""
    monkeypatch.setattr(exotel, "datetime", _FixedDatetime)
    await _set_host_window(
        db_session,
        test_user,
        host_call_hours_enabled=True,
        host_call_hours_start="18:00",
        host_call_hours_end="22:00",
        host_call_hours_timezone="Asia/Kolkata",
    )
    property_ = await _property_with(db_session, test_user)
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_4", "To": property_.exophone},
    )
    assert resp.status_code == 200


# 4. Overnight host window -----------------------------------------------------


async def test_overnight_window_currently_in_host_hours_returns_302(client, db_session, test_user, monkeypatch):
    """Clock at 2026-08-12 00:30 IST -- squarely inside a 22:00->06:00
    overnight window via the wraparound."""

    class _MidnightIST(datetime):
        @classmethod
        def now(cls, tz=None):
            # 2026-08-11 19:00 UTC = 2026-08-12 00:30 IST.
            return cls(2026, 8, 11, 19, 0, tzinfo=tz) if tz else cls(2026, 8, 11, 19, 0)

    monkeypatch.setattr(exotel, "datetime", _MidnightIST)
    await _set_host_window(
        db_session,
        test_user,
        host_call_hours_enabled=True,
        host_call_hours_start="22:00",
        host_call_hours_end="06:00",
        host_call_hours_timezone="Asia/Kolkata",
    )
    property_ = await _property_with(db_session, test_user)
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_5", "To": property_.exophone},
        follow_redirects=False,
    )
    assert resp.status_code == 302


async def test_overnight_window_outside_host_hours_returns_200(client, db_session, test_user, monkeypatch):
    """Same overnight 22:00->06:00 window, clock at 06:30 IST -- just past
    the 06:00 end boundary -> MIRA."""

    class _JustAfterWindow(datetime):
        @classmethod
        def now(cls, tz=None):
            # 2026-08-12 01:00 UTC = 2026-08-12 06:30 IST.
            return cls(2026, 8, 12, 1, 0, tzinfo=tz) if tz else cls(2026, 8, 12, 1, 0)

    monkeypatch.setattr(exotel, "datetime", _JustAfterWindow)
    await _set_host_window(
        db_session,
        test_user,
        host_call_hours_enabled=True,
        host_call_hours_start="22:00",
        host_call_hours_end="06:00",
        host_call_hours_timezone="Asia/Kolkata",
    )
    property_ = await _property_with(db_session, test_user)
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_6", "To": property_.exophone},
    )
    assert resp.status_code == 200


# 5. Timezone boundary --------------------------------------------------------


async def test_timezone_boundary_exact_start_returns_302(client, db_session, test_user, monkeypatch):
    """Exact window start, [start, end) inclusive -> HOST. Clock:
    2026-08-11 05:30 UTC = 11:00:00 IST exactly, host hours 11:00-17:00."""

    class _ExactStart(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 11, 5, 30, tzinfo=tz) if tz else cls(2026, 8, 11, 5, 30)

    monkeypatch.setattr(exotel, "datetime", _ExactStart)
    await _set_host_window(
        db_session,
        test_user,
        host_call_hours_enabled=True,
        host_call_hours_start="11:00",
        host_call_hours_end="17:00",
        host_call_hours_timezone="Asia/Kolkata",
    )
    property_ = await _property_with(db_session, test_user)
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_7", "To": property_.exophone},
        follow_redirects=False,
    )
    assert resp.status_code == 302


async def test_timezone_boundary_exact_end_returns_200(client, db_session, test_user, monkeypatch):
    """Exact window end, exclusive -> MIRA. Clock:
    2026-08-11 11:30 UTC = 17:00:00 IST exactly."""

    class _ExactEnd(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 11, 11, 30, tzinfo=tz) if tz else cls(2026, 8, 11, 11, 30)

    monkeypatch.setattr(exotel, "datetime", _ExactEnd)
    await _set_host_window(
        db_session,
        test_user,
        host_call_hours_enabled=True,
        host_call_hours_start="11:00",
        host_call_hours_end="17:00",
        host_call_hours_timezone="Asia/Kolkata",
    )
    property_ = await _property_with(db_session, test_user)
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_8", "To": property_.exophone},
    )
    assert resp.status_code == 200


# 6. Unknown DID/property ----------------------------------------------------


async def test_unknown_dialed_number_returns_200(client):
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_9", "To": "+919999999999", "From": "+918888888888"},
    )
    assert resp.status_code == 200


async def test_missing_dialed_number_returns_200(client):
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_10"},
    )
    assert resp.status_code == 200


# 7. Missing CallSid --------------------------------------------------------


async def test_missing_call_sid_returns_200(client, db_session, test_user):
    await _set_host_window(
        db_session,
        test_user,
        host_call_hours_enabled=True,
        host_call_hours_start="00:00",
        host_call_hours_end="23:59",
        host_call_hours_timezone="Asia/Kolkata",
    )
    property_ = await _property_with(db_session, test_user)
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "To": property_.exophone},
    )
    # Even an always-HOST window must not be routed to HOST without a
    # resolvable CallSid -- fail-closed applies before property/ownership
    # resolution is even attempted.
    assert resp.status_code == 200


# 8. Invalid window configuration ------------------------------------------


async def test_enabled_window_missing_bounds_returns_200(client, db_session, test_user):
    """host_call_hours_enabled with no start/end configured (e.g. written
    directly against the DB, bypassing the API validator) --
    InvalidCallOwnershipConfigError from the resolver, caught and defaulted
    to MIRA, not propagated as a 500."""
    await _set_host_window(
        db_session,
        test_user,
        host_call_hours_enabled=True,
        host_call_hours_start=None,
        host_call_hours_end=None,
    )
    property_ = await _property_with(db_session, test_user)
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_12", "To": property_.exophone},
    )
    assert resp.status_code == 200


async def test_enabled_window_invalid_timezone_returns_200(client, db_session, test_user):
    await _set_host_window(
        db_session,
        test_user,
        host_call_hours_enabled=True,
        host_call_hours_start="09:00",
        host_call_hours_end="17:00",
        host_call_hours_timezone="Not/A_Real_Zone",
    )
    property_ = await _property_with(db_session, test_user)
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_13", "To": property_.exophone},
    )
    assert resp.status_code == 200


# 9. Resolver error --------------------------------------------------------


async def test_resolver_exception_is_caught_and_defaults_to_200(client, db_session, test_user, monkeypatch):
    """Any unexpected exception from the resolver (not just
    InvalidCallOwnershipConfigError) must still fail closed to MIRA, never
    propagate as a 500 that could produce Exotel-undefined behavior."""
    property_ = await _property_with(db_session, test_user)

    def _boom(property_, host, current_time_utc):
        raise RuntimeError("simulated unexpected resolver failure")

    monkeypatch.setattr(exotel.call_ownership, "resolve_effective_call_owner", _boom)
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_14", "To": property_.exophone},
    )
    assert resp.status_code == 200


# 10. Database failure ----------------------------------------------------------


async def test_property_lookup_failure_is_caught_and_defaults_to_200(client, monkeypatch):
    """A DB-layer failure during property resolution (simulated) must also
    fail closed -- the property-lookup call sits outside the resolver's own
    try/except, so this confirms that earlier failure mode is covered too."""

    async def _boom(db, dialed_number):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(exotel.call_service, "get_property_by_number", _boom)
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_15", "To": "+919999999999"},
    )
    assert resp.status_code == 200


# 11. Existing property behavior remains MIRA ----------------------------------


async def test_existing_property_fixture_defaults_to_mira(client, test_property):
    """test_property (the shared conftest fixture) with a test_user whose
    host_call_hours_enabled is the server default (False) -- routes to MIRA
    through this endpoint, unchanged."""
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_16", "To": test_property.exophone},
    )
    assert resp.status_code == 200


# Auth -----------------------------------------------------------------------


async def test_missing_token_returns_200_not_error(client):
    """Unlike exotel_call_status (which returns a JSON {"error": ...} body
    on bad auth), this endpoint must still answer Exotel's Passthru
    contract with a plain status code -- 200, the same fail-closed default
    as every other error path."""
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"CallSid": "call_17", "To": "+919999999999"},
    )
    assert resp.status_code == 200


async def test_wrong_token_returns_200(client):
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "wrong", "CallSid": "call_18", "To": "+919999999999"},
    )
    assert resp.status_code == 200
