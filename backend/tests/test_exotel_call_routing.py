"""Phase 4: GET /webhooks/exotel/call-routing -- the initial call-ownership
Passthru. Every test uses a fixed clock (monkeypatch on the endpoint
module's own `datetime`, same pattern as test_system_prompt.py) --
datetime.now() is never called directly in these tests. Fail-closed
scenarios (missing CallSid, unknown DID, invalid config, resolver error) all
assert HTTP 200 (MIRA), never 302 -- routing an unresolvable call to a host
who never opted in would be the real incident here, not a guest reaching
Mira when something else was intended.
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


# 1. MIRA mode -----------------------------------------------------------------


async def test_mira_mode_returns_200(client, db_session, test_user):
    property_ = await _property_with(db_session, test_user, call_handling_mode="MIRA")
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_1", "To": property_.exophone, "From": "+919999999999"},
    )
    assert resp.status_code == 200


# 2. HOST mode -------------------------------------------------------------------


async def test_host_mode_returns_302(client, db_session, test_user):
    property_ = await _property_with(db_session, test_user, call_handling_mode="HOST")
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_2", "To": property_.exophone, "From": "+919999999999"},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# 3. SCHEDULED during host hours ---------------------------------------------------


async def test_scheduled_during_host_hours_returns_302(client, db_session, test_user, monkeypatch):
    """Fixed clock: 2026-08-11 06:30 UTC = 12:00 IST -- inside an
    11:00-17:00 host-hours window."""
    monkeypatch.setattr(exotel, "datetime", _FixedDatetime)
    property_ = await _property_with(
        db_session,
        test_user,
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="11:00",
        call_handling_schedule_end="17:00",
        timezone="Asia/Kolkata",
    )
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_3", "To": property_.exophone},
        follow_redirects=False,
    )
    assert resp.status_code == 302


# 4. SCHEDULED outside host hours --------------------------------------------------


async def test_scheduled_outside_host_hours_returns_200(client, db_session, test_user, monkeypatch):
    """Same fixed clock (12:00 IST) but host hours are 18:00-22:00 --
    outside the window -> MIRA."""
    monkeypatch.setattr(exotel, "datetime", _FixedDatetime)
    property_ = await _property_with(
        db_session,
        test_user,
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="18:00",
        call_handling_schedule_end="22:00",
        timezone="Asia/Kolkata",
    )
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_4", "To": property_.exophone},
    )
    assert resp.status_code == 200


# 5. Overnight host schedule -----------------------------------------------------


async def test_scheduled_overnight_window_currently_in_host_hours_returns_302(
    client, db_session, test_user, monkeypatch
):
    """Fixed clock 06:30 UTC = 12:00 IST -- inside a 22:00->06:00 overnight
    window only via the wraparound (00:00-06:00 segment is well past this
    clock's 12:00, so use a clock that actually falls inside: 00:30 IST)."""

    class _MidnightIST(datetime):
        @classmethod
        def now(cls, tz=None):
            # 2026-08-11 19:00 UTC = 2026-08-12 00:30 IST -- squarely inside
            # a 22:00->06:00 overnight window (same UTC/local-day-boundary
            # case exercised in test_call_ownership.py).
            return cls(2026, 8, 11, 19, 0, tzinfo=tz) if tz else cls(2026, 8, 11, 19, 0)

    monkeypatch.setattr(exotel, "datetime", _MidnightIST)
    property_ = await _property_with(
        db_session,
        test_user,
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="22:00",
        call_handling_schedule_end="06:00",
        timezone="Asia/Kolkata",
    )
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_5", "To": property_.exophone},
        follow_redirects=False,
    )
    assert resp.status_code == 302


async def test_scheduled_overnight_window_outside_host_hours_returns_200(client, db_session, test_user, monkeypatch):
    """Same overnight 22:00->06:00 window, fixed clock at 06:30 IST -- just
    past the 06:00 end boundary -> MIRA."""

    class _JustAfterWindow(datetime):
        @classmethod
        def now(cls, tz=None):
            # 2026-08-12 01:00 UTC = 2026-08-12 06:30 IST.
            return cls(2026, 8, 12, 1, 0, tzinfo=tz) if tz else cls(2026, 8, 12, 1, 0)

    monkeypatch.setattr(exotel, "datetime", _JustAfterWindow)
    property_ = await _property_with(
        db_session,
        test_user,
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="22:00",
        call_handling_schedule_end="06:00",
        timezone="Asia/Kolkata",
    )
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_6", "To": property_.exophone},
    )
    assert resp.status_code == 200


# 6. Timezone boundary -----------------------------------------------------------


async def test_timezone_boundary_exact_start_returns_302(client, db_session, test_user, monkeypatch):
    """Exact schedule start, [start, end) inclusive -> HOST. Fixed clock:
    2026-08-11 05:30 UTC = 11:00:00 IST exactly, host hours 11:00-17:00."""

    class _ExactStart(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 11, 5, 30, tzinfo=tz) if tz else cls(2026, 8, 11, 5, 30)

    monkeypatch.setattr(exotel, "datetime", _ExactStart)
    property_ = await _property_with(
        db_session,
        test_user,
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="11:00",
        call_handling_schedule_end="17:00",
        timezone="Asia/Kolkata",
    )
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_7", "To": property_.exophone},
        follow_redirects=False,
    )
    assert resp.status_code == 302


async def test_timezone_boundary_exact_end_returns_200(client, db_session, test_user, monkeypatch):
    """Exact schedule end, exclusive -> MIRA. Fixed clock:
    2026-08-11 11:30 UTC = 17:00:00 IST exactly."""

    class _ExactEnd(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 11, 11, 30, tzinfo=tz) if tz else cls(2026, 8, 11, 11, 30)

    monkeypatch.setattr(exotel, "datetime", _ExactEnd)
    property_ = await _property_with(
        db_session,
        test_user,
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="11:00",
        call_handling_schedule_end="17:00",
        timezone="Asia/Kolkata",
    )
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_8", "To": property_.exophone},
    )
    assert resp.status_code == 200


# 7. Unknown DID/property ---------------------------------------------------------


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


# 8. Missing CallSid ---------------------------------------------------------------


async def test_missing_call_sid_returns_200(client, db_session, test_user):
    property_ = await _property_with(db_session, test_user, call_handling_mode="HOST")
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "To": property_.exophone},
    )
    # Even a HOST-mode property must not be routed to HOST without a
    # resolvable CallSid -- fail-closed applies before property/ownership
    # resolution is even attempted.
    assert resp.status_code == 200


# 9. Invalid property configuration ------------------------------------------------


async def test_invalid_call_handling_mode_returns_200(client, db_session, test_user):
    """A property row with a call_handling_mode value outside MIRA/HOST/
    SCHEDULED (e.g. written directly against the DB, bypassing the API's
    own validator) must not crash the endpoint or route to HOST."""
    property_ = await _property_with(db_session, test_user, call_handling_mode="GARBAGE")
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_11", "To": property_.exophone},
    )
    assert resp.status_code == 200


async def test_scheduled_missing_schedule_returns_200(client, db_session, test_user):
    """SCHEDULED with no start/end configured -- InvalidCallOwnershipConfigError
    from the resolver, caught and defaulted to MIRA, not propagated as a 500."""
    property_ = await _property_with(db_session, test_user, call_handling_mode="SCHEDULED")
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_12", "To": property_.exophone},
    )
    assert resp.status_code == 200


async def test_scheduled_invalid_timezone_returns_200(client, db_session, test_user):
    property_ = await _property_with(
        db_session,
        test_user,
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="09:00",
        call_handling_schedule_end="17:00",
        timezone="Not/A_Real_Zone",
    )
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_13", "To": property_.exophone},
    )
    assert resp.status_code == 200


# 10. Resolver error ----------------------------------------------------------------


async def test_resolver_exception_is_caught_and_defaults_to_200(client, db_session, test_user, monkeypatch):
    """Any unexpected exception from the resolver (not just
    InvalidCallOwnershipConfigError) must still fail closed to MIRA, never
    propagate as a 500 that could produce Exotel-undefined behavior."""
    property_ = await _property_with(db_session, test_user, call_handling_mode="MIRA")

    def _boom(property_, current_time_utc):
        raise RuntimeError("simulated unexpected resolver failure")

    monkeypatch.setattr(exotel.call_ownership, "resolve_effective_call_owner", _boom)
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_14", "To": property_.exophone},
    )
    assert resp.status_code == 200


# 11. Database failure ---------------------------------------------------------------


async def test_property_lookup_failure_is_caught_and_defaults_to_200(client, monkeypatch):
    """A DB-layer failure during property resolution (simulated here, since
    a real connection drop isn't practical in this test suite) must also
    fail closed -- the property-lookup call sits outside the resolver's own
    try/except, so this specifically confirms that earlier failure mode is
    covered too, not just resolver-internal errors."""

    async def _boom(db, dialed_number):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(exotel.call_service, "get_property_by_number", _boom)
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_15", "To": "+919999999999"},
    )
    assert resp.status_code == 200


# 12. Existing property behavior remains MIRA ----------------------------------------


async def test_existing_property_fixture_defaults_to_mira(client, test_property):
    """test_property (the shared conftest fixture, used across the whole
    suite) never sets call_handling_mode -- confirms a pre-Phase-1 property
    shape still routes to MIRA through this new endpoint, unchanged."""
    resp = await client.get(
        "/api/v1/webhooks/exotel/call-routing",
        params={"token": "test-token", "CallSid": "call_16", "To": test_property.exophone},
    )
    assert resp.status_code == 200


# Auth -------------------------------------------------------------------------------


async def test_missing_token_returns_200_not_error(client):
    """Unlike exotel_call_status (which returns a JSON {"error": ...} body
    on bad auth), this endpoint must still answer Exotel's Passthru
    contract with a plain status code -- 200, the same fail-closed default
    as every other error path, not a body Exotel's synchronous-Passthru
    parser was never designed to read."""
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
