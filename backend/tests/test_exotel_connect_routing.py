"""GET /webhooks/exotel/connect-routing -- the Exotel Connect applet's
dynamic destination lookup. Every failure case must resolve to HTTP 200
with an empty destination.numbers list (never a fabricated/static number,
never a caller-supplied one, never a 5xx) -- see the endpoint's own
docstring for why an empty list is this codebase's uniform "cannot safely
route this call" signal.

Routing input as of documentation/host-call-hours-and-handoff.md: the
account-global window on User (host_call_hours_*). "Initial HOST call"
tests configure an always-on window on test_user; the live-handoff tests
leave the window disabled to prove the handoff path is independently
authorized.
"""

import uuid
from datetime import datetime

from app.api.v1.webhooks import exotel
from app.models.call_session import CallSession
from app.models.property import Property
from app.services import call_service


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        # 2026-08-11 06:30 UTC = 12:00 IST.
        return cls(2026, 8, 11, 6, 30, tzinfo=tz) if tz else cls(2026, 8, 11, 6, 30)


async def _property_with(db_session, test_user, **overrides) -> Property:
    defaults = dict(
        user_id=test_user.id,
        name="Connect Routing Test Villa",
        base_price=1000,
        exophone=f"+9180{uuid.uuid4().int % 10**8:08d}",
    )
    defaults.update(overrides)
    property_ = Property(**defaults)
    db_session.add(property_)
    await db_session.commit()
    await db_session.refresh(property_)
    return property_


async def _enable_always_on_host_window(db_session, test_user) -> None:
    """Configure test_user so resolve_effective_call_owner returns HOST at
    any time of day -- the connect-routing "initial HOST call" precondition."""
    test_user.host_call_hours_enabled = True
    test_user.host_call_hours_start = "00:00"
    test_user.host_call_hours_end = "23:59"
    test_user.host_call_hours_timezone = "Asia/Kolkata"
    db_session.add(test_user)
    await db_session.commit()
    await db_session.refresh(test_user)


async def _set_phone(db_session, test_user, phone) -> None:
    test_user.phone = phone
    db_session.add(test_user)
    await db_session.commit()


async def _call_session_for(db_session, property_, **overrides) -> CallSession:
    defaults = dict(
        exotel_call_id=f"call-{uuid.uuid4().hex[:8]}",
        user_id=property_.user_id,
        property_id=property_.id,
        caller_number="+919999999999",
        status="in_progress",
    )
    defaults.update(overrides)
    session = CallSession(**defaults)
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    return session


def _numbers(resp) -> list:
    return resp.json()["destination"]["numbers"]


# 1. Valid initial HOST call ------------------------------------------------


async def test_valid_initial_host_call_returns_host_phone(client, db_session, test_user):
    await _set_phone(db_session, test_user, "+919812345678")
    await _enable_always_on_host_window(db_session, test_user)
    property_ = await _property_with(db_session, test_user)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_1", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == ["+919812345678"]


async def test_valid_initial_host_call_during_configured_host_hours(client, db_session, test_user, monkeypatch):
    monkeypatch.setattr(exotel, "datetime", _FixedDatetime)
    await _set_phone(db_session, test_user, "+919812345678")
    test_user.host_call_hours_enabled = True
    test_user.host_call_hours_start = "11:00"
    test_user.host_call_hours_end = "17:00"
    test_user.host_call_hours_timezone = "Asia/Kolkata"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_2", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == ["+919812345678"]


async def test_lead_agent_number_initial_host_call_during_host_hours(client, db_session, test_user, monkeypatch):
    """Same as test_valid_initial_host_call_during_configured_host_hours,
    but the dialed number is the account's Lead Agent line (no property in
    scope at all) rather than a property's own exophone -- regression
    coverage for the bug where this path only ever tried
    get_property_by_number and refused to route the instant that returned
    None."""
    monkeypatch.setattr(exotel, "datetime", _FixedDatetime)
    await _set_phone(db_session, test_user, "+919812345678")
    test_user.lead_exophone = "+911141185313"
    test_user.host_call_hours_enabled = True
    test_user.host_call_hours_start = "11:00"
    test_user.host_call_hours_end = "17:00"
    test_user.host_call_hours_timezone = "Asia/Kolkata"
    db_session.add(test_user)
    await db_session.commit()

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_lead_1", "To": test_user.lead_exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == ["+919812345678"]


# 2. Valid live handoff ----------------------------------------------------


async def test_valid_live_handoff_returns_host_phone(client, db_session, test_user):
    """Window disabled (so the initial-HOST resolution path would itself
    refuse to route) but a CallSession with handoff_status="requested" --
    confirms the handoff path is independently authorized, not merely
    falling through to a HOST resolution that happens to also succeed."""
    await _set_phone(db_session, test_user, "+919812345678")
    property_ = await _property_with(db_session, test_user)
    session = await _call_session_for(db_session, property_, handoff_status="requested")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": session.exotel_call_id},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == ["+919812345678"]


# 3. Invalid CallSid -----------------------------------------------------------


async def test_unknown_call_sid_with_no_property_returns_empty(client):
    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "totally-unknown-call-sid"},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_missing_call_sid_returns_empty(client, db_session, test_user):
    await _set_phone(db_session, test_user, "+919812345678")
    await _enable_always_on_host_window(db_session, test_user)
    property_ = await _property_with(db_session, test_user)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


# 4. Ended call --------------------------------------------------------------


async def test_ended_call_with_requested_handoff_returns_empty(client, db_session, test_user):
    await _set_phone(db_session, test_user, "+919812345678")
    property_ = await _property_with(db_session, test_user)
    session = await _call_session_for(
        db_session, property_, handoff_status="requested", status="completed"
    )

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": session.exotel_call_id},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


# 5. Missing host ----------------------------------------------------------


async def test_property_with_no_resolvable_host_returns_empty(client, db_session, test_user, monkeypatch):
    """Simulated via a monkeypatched User lookup rather than a dangling
    property.user_id -- properties.user_id carries a real FK constraint
    (ondelete=CASCADE) to users.id, so a genuinely orphaned property row
    cannot exist in this schema."""
    await _enable_always_on_host_window(db_session, test_user)
    property_ = await _property_with(db_session, test_user)

    from sqlalchemy.ext.asyncio import AsyncSession

    _real_get = AsyncSession.get

    async def _no_host(self, model, ident, *args, **kwargs):
        if model is exotel.User:
            return None
        return await _real_get(self, model, ident, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", _no_host)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_missing_host", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


# 6. Missing phone ----------------------------------------------------------


async def test_host_with_no_phone_returns_empty(client, db_session, test_user):
    # test_user fixture never sets .phone -- defaults to None.
    await _enable_always_on_host_window(db_session, test_user)
    property_ = await _property_with(db_session, test_user)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_no_phone", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


# 7. Invalid phone --------------------------------------------------------


async def test_host_with_unparseable_phone_returns_empty(client, db_session, test_user):
    await _set_phone(db_session, test_user, "not-a-number")
    await _enable_always_on_host_window(db_session, test_user)
    property_ = await _property_with(db_session, test_user)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_bad_phone", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_host_with_too_few_digits_returns_empty(client, db_session, test_user):
    await _set_phone(db_session, test_user, "12345")
    await _enable_always_on_host_window(db_session, test_user)
    property_ = await _property_with(db_session, test_user)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_short_phone", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_host_phone_with_trailing_garbage_is_sanitized_not_passed_through(client, db_session, test_user):
    """Regression: User.phone has no format validator (app/schemas/user.py
    accepts any string up to 32 chars), so a host's stored phone can
    contain more than a clean number -- e.g. a copy-paste artifact. The
    endpoint must never return that raw string verbatim; only the
    normalized digits."""
    await _set_phone(db_session, test_user, "9876543210; rm -rf /")
    await _enable_always_on_host_window(db_session, test_user)
    property_ = await _property_with(db_session, test_user)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_dirty_phone", "To": property_.exophone},
    )
    assert resp.status_code == 200
    numbers = _numbers(resp)
    assert numbers == ["+919876543210"]
    assert "rm -rf" not in numbers[0]


async def test_host_phone_with_internal_whitespace_and_punctuation_is_normalized(client, db_session, test_user):
    await _set_phone(db_session, test_user, "  +91 98765 43210  ")
    await _enable_always_on_host_window(db_session, test_user)
    property_ = await _property_with(db_session, test_user)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_spaced_phone", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == ["+919876543210"]


# 8. Unauthorized property (window disabled, no handoff) --------------------


async def test_window_disabled_property_with_no_handoff_returns_empty(client, db_session, test_user):
    """No CallSession at all, and the host's window is disabled -- neither
    routable path applies, must not route."""
    await _set_phone(db_session, test_user, "+919812345678")
    property_ = await _property_with(db_session, test_user)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_unauthorized", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


# 9. Invalid handoff state ------------------------------------------------


async def test_call_session_with_null_handoff_status_falls_back_to_ownership_resolution(
    client, db_session, test_user
):
    """A CallSession exists but handoff_status is still NULL -- not a valid
    handoff, must fall back to the initial-HOST ownership resolution path,
    which itself refuses (window disabled)."""
    await _set_phone(db_session, test_user, "+919812345678")
    property_ = await _property_with(db_session, test_user)
    session = await _call_session_for(db_session, property_, handoff_status=None)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": session.exotel_call_id, "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_call_session_with_connecting_handoff_status_is_not_routable(client, db_session, test_user):
    """A handoff_status of "connecting" (a future lifecycle value) must NOT
    be treated as routable -- only the exact "requested" value is."""
    await _set_phone(db_session, test_user, "+919812345678")
    property_ = await _property_with(db_session, test_user)
    session = await _call_session_for(db_session, property_, handoff_status="connecting")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": session.exotel_call_id, "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_handoff_with_no_property_id_still_routes_via_user_id(client, db_session, test_user):
    """Regression: a Lead Agent (portfolio-wide) call's live handoff has
    property_id=NULL by design (see CallSession.property_id's own model
    comment) but user_id is always set whenever a host is known. This must
    still route to the host's phone -- requiring property_id here used to
    refuse every Lead Agent handoff outright, even though the host was
    known the whole time."""
    await _set_phone(db_session, test_user, "+919812345678")
    session = CallSession(
        exotel_call_id=f"call-{uuid.uuid4().hex[:8]}",
        user_id=test_user.id,
        property_id=None,
        caller_number="+919999999999",
        status="in_progress",
        handoff_status="requested",
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": session.exotel_call_id},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == ["+919812345678"]


async def test_handoff_with_no_property_id_and_no_resolvable_host_returns_empty(client, db_session):
    """A CallSession with property_id AND user_id both NULL (shouldn't
    happen in practice, but the endpoint must still fail closed) -- no host
    to route to at all."""
    session = CallSession(
        exotel_call_id=f"call-{uuid.uuid4().hex[:8]}",
        user_id=None,
        property_id=None,
        caller_number="+919999999999",
        status="in_progress",
        handoff_status="requested",
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": session.exotel_call_id},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


# 10. DB failure ------------------------------------------------------------


async def test_db_failure_during_call_session_lookup_returns_empty(client, monkeypatch):
    async def _boom(self, *args, **kwargs):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr("sqlalchemy.ext.asyncio.AsyncSession.scalar", _boom)
    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_db_failure", "To": "+919999999999"},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_resolver_exception_is_caught_and_returns_empty(client, db_session, test_user, monkeypatch):
    await _set_phone(db_session, test_user, "+919812345678")
    property_ = await _property_with(db_session, test_user)

    def _boom(property_, host, current_time_utc):
        raise RuntimeError("simulated unexpected resolver failure")

    monkeypatch.setattr(exotel.call_ownership, "resolve_effective_call_owner", _boom)
    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_resolver_boom", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_invalid_ownership_config_is_caught_and_returns_empty(client, db_session, test_user):
    await _set_phone(db_session, test_user, "+919812345678")
    # Enabled window with no bounds -> InvalidCallOwnershipConfigError from
    # the resolver, caught and returned as an empty destination.
    test_user.host_call_hours_enabled = True
    test_user.host_call_hours_start = None
    test_user.host_call_hours_end = None
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_bad_config", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


# 11. Arbitrary destination injection attempt ------------------------------


async def test_arbitrary_destination_query_param_is_ignored(client, db_session, test_user):
    """The endpoint must never read a caller-supplied destination -- only
    CallSid/To are ever read."""
    await _set_phone(db_session, test_user, "+919812345678")
    await _enable_always_on_host_window(db_session, test_user)
    property_ = await _property_with(db_session, test_user)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={
            "token": "test-token",
            "CallSid": "connect_injection",
            "To": property_.exophone,
            "Destination": "+911111111111",
            "Number": "+912222222222",
            "PhoneNumber": "+913333333333",
        },
    )
    assert resp.status_code == 200
    assert _numbers(resp) == ["+919812345678"]


async def test_arbitrary_destination_cannot_override_handoff_routing(client, db_session, test_user):
    await _set_phone(db_session, test_user, "+919812345678")
    property_ = await _property_with(db_session, test_user)
    session = await _call_session_for(db_session, property_, handoff_status="requested")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={
            "token": "test-token",
            "CallSid": session.exotel_call_id,
            "Destination": "+919000000000",
        },
    )
    assert resp.status_code == 200
    assert _numbers(resp) == ["+919812345678"]


# Regression: on_pipeline_finished's real finalize_call_session sequence ----


async def test_call_finalized_as_completed_by_default_cannot_be_routed(client, db_session, test_user):
    """Baseline for the bug below: finalize_call_session's own default
    (status="completed") -- confirming this refuses to route establishes
    that the "in_progress" override in pipeline.py's on_pipeline_finished
    is load-bearing, not redundant."""
    await _set_phone(db_session, test_user, "+919812345678")
    property_ = await _property_with(db_session, test_user)
    session = await _call_session_for(db_session, property_, handoff_status="requested")

    await call_service.finalize_call_session(db_session, session.id, transcript="guest: hi\nassistant: hello")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": session.exotel_call_id},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_call_finalized_as_in_progress_for_handoff_can_still_be_routed(client, db_session, test_user):
    """The actual fix: pipeline.py's on_pipeline_finished calls
    finalize_call_session(..., status="in_progress") when is_host_handoff
    is True. This reproduces that exact call and confirms connect-routing
    can still find and route the call afterward."""
    await _set_phone(db_session, test_user, "+919812345678")
    property_ = await _property_with(db_session, test_user)
    session = await _call_session_for(db_session, property_, handoff_status="requested")

    await call_service.finalize_call_session(
        db_session, session.id, transcript="guest: hi\nassistant: hello", status="in_progress"
    )

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": session.exotel_call_id},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == ["+919812345678"]


# Auth --------------------------------------------------------------------


async def test_missing_token_returns_empty(client):
    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"CallSid": "connect_no_token", "To": "+919999999999"},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_wrong_token_returns_empty(client, db_session, test_user):
    await _set_phone(db_session, test_user, "+919812345678")
    await _enable_always_on_host_window(db_session, test_user)
    property_ = await _property_with(db_session, test_user)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "wrong", "CallSid": "connect_wrong_token", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


# Response shape --------------------------------------------------------------


async def test_response_shape_has_no_extra_fields(client, db_session, test_user):
    await _set_phone(db_session, test_user, "+919812345678")
    await _enable_always_on_host_window(db_session, test_user)
    property_ = await _property_with(db_session, test_user)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_shape", "To": property_.exophone},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"destination"}
    assert set(body["destination"].keys()) == {"numbers"}
