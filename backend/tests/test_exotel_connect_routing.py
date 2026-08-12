"""Phase 8: GET /webhooks/exotel/connect-routing -- the Exotel Connect
applet's dynamic destination lookup. Every failure case must resolve to
HTTP 200 with an empty destination.numbers list (never a fabricated/static
number, never a caller-supplied one, never a 5xx) -- see the endpoint's
own docstring for why an empty list is this codebase's uniform "cannot
safely route this call" signal.
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


# 1. Valid initial HOST call ----------------------------------------------------------


async def test_valid_initial_host_call_returns_host_phone(client, db_session, test_user):
    test_user.phone = "+919812345678"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="HOST")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_1", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == ["+919812345678"]


async def test_valid_initial_host_call_scheduled_mode_during_host_hours(client, db_session, test_user, monkeypatch):
    monkeypatch.setattr(exotel, "datetime", _FixedDatetime)
    test_user.phone = "+919812345678"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(
        db_session,
        test_user,
        call_handling_mode="SCHEDULED",
        call_handling_schedule_start="11:00",
        call_handling_schedule_end="17:00",
        timezone="Asia/Kolkata",
    )

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_2", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == ["+919812345678"]


# 2. Valid live handoff -----------------------------------------------------------------


async def test_valid_live_handoff_returns_host_phone(client, db_session, test_user):
    """MIRA-mode property (so the initial-HOST resolution path would
    itself refuse to route) but a CallSession with handoff_status=
    "requested" -- confirms the handoff path is independently authorized,
    not merely falling through to a HOST-mode resolution that happens to
    also succeed."""
    test_user.phone = "+919812345678"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="MIRA")
    session = await _call_session_for(db_session, property_, handoff_status="requested")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": session.exotel_call_id},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == ["+919812345678"]


# 3. Invalid CallSid ----------------------------------------------------------------


async def test_unknown_call_sid_with_no_property_returns_empty(client):
    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "totally-unknown-call-sid"},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_missing_call_sid_returns_empty(client, db_session, test_user):
    test_user.phone = "+919812345678"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="HOST")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


# 4. Ended call -----------------------------------------------------------------------


async def test_ended_call_with_requested_handoff_returns_empty(client, db_session, test_user):
    test_user.phone = "+919812345678"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="MIRA")
    session = await _call_session_for(
        db_session, property_, handoff_status="requested", status="completed"
    )

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": session.exotel_call_id},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


# 5. Missing host ------------------------------------------------------------------


async def test_property_with_no_resolvable_host_returns_empty(client, db_session, test_user, monkeypatch):
    """Simulated via a monkeypatched User lookup rather than a dangling
    property.user_id -- properties.user_id carries a real FK constraint
    (ondelete=CASCADE) to users.id, so a genuinely orphaned property row
    cannot exist in this schema; this is how call-routing's own equivalent
    "DB lookup fails" cases are simulated too (see
    test_exotel_call_routing.py's test_property_lookup_failure_is_caught_
    and_defaults_to_200)."""
    property_ = await _property_with(db_session, test_user, call_handling_mode="HOST")

    async def _no_host(self, model, ident, *args, **kwargs):
        if model is exotel.User:
            return None
        return await _real_get(self, model, ident, *args, **kwargs)

    from sqlalchemy.ext.asyncio import AsyncSession

    _real_get = AsyncSession.get
    monkeypatch.setattr(AsyncSession, "get", _no_host)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_missing_host", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


# 6. Missing phone ------------------------------------------------------------------


async def test_host_with_no_phone_returns_empty(client, db_session, test_user):
    # test_user fixture never sets .phone -- defaults to None.
    property_ = await _property_with(db_session, test_user, call_handling_mode="HOST")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_no_phone", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


# 7. Invalid phone ------------------------------------------------------------------


async def test_host_with_unparseable_phone_returns_empty(client, db_session, test_user):
    test_user.phone = "not-a-number"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="HOST")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_bad_phone", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_host_with_too_few_digits_returns_empty(client, db_session, test_user):
    test_user.phone = "12345"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="HOST")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_short_phone", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_host_phone_with_trailing_garbage_is_sanitized_not_passed_through(client, db_session, test_user):
    """Regression for a Phase 8 review finding: User.phone has no format
    validator anywhere (app/schemas/user.py accepts any string up to 32
    chars), so a host's stored phone can contain more than just a clean
    number -- e.g. a copy-paste artifact. The endpoint must never return
    that raw string verbatim; it must return only the normalized digits."""
    test_user.phone = "9876543210; rm -rf /"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="HOST")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_dirty_phone", "To": property_.exophone},
    )
    assert resp.status_code == 200
    numbers = _numbers(resp)
    assert numbers == ["+919876543210"]
    assert "rm -rf" not in numbers[0]


async def test_host_phone_with_internal_whitespace_and_punctuation_is_normalized(client, db_session, test_user):
    test_user.phone = "  +91 98765 43210  "
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="HOST")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_spaced_phone", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == ["+919876543210"]


# 8. Unauthorized property (MIRA mode, no handoff) -----------------------------------


async def test_mira_mode_property_with_no_handoff_returns_empty(client, db_session, test_user):
    """No CallSession at all, and the property is MIRA-mode -- neither
    routable path applies, must not route."""
    test_user.phone = "+919812345678"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="MIRA")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_unauthorized", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


# 9. Invalid handoff state ------------------------------------------------------------


async def test_call_session_with_null_handoff_status_falls_back_to_ownership_resolution(
    client, db_session, test_user
):
    """A CallSession exists (e.g. attach_exotel_call already ran off the
    status callback) but handoff_status is still NULL -- not a valid
    handoff, must fall back to the initial-HOST ownership resolution path,
    which itself refuses (MIRA mode)."""
    test_user.phone = "+919812345678"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="MIRA")
    session = await _call_session_for(db_session, property_, handoff_status=None)

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": session.exotel_call_id, "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_call_session_with_connecting_handoff_status_is_not_routable(client, db_session, test_user):
    """A handoff_status of "connecting" (a future lifecycle value, not yet
    written by anything today) must NOT be treated as routable -- only the
    exact "requested" value is."""
    test_user.phone = "+919812345678"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="MIRA")
    session = await _call_session_for(db_session, property_, handoff_status="connecting")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": session.exotel_call_id, "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_handoff_with_no_property_id_returns_empty(client, db_session, test_user):
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
    assert _numbers(resp) == []


# 10. DB failure ------------------------------------------------------------------


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
    test_user.phone = "+919812345678"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="MIRA")

    def _boom(property_, current_time_utc):
        raise RuntimeError("simulated unexpected resolver failure")

    monkeypatch.setattr(exotel.call_ownership, "resolve_effective_call_owner", _boom)
    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_resolver_boom", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_invalid_ownership_config_is_caught_and_returns_empty(client, db_session, test_user):
    test_user.phone = "+919812345678"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="SCHEDULED")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_bad_config", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


# 11. Arbitrary destination injection attempt ------------------------------------------


async def test_arbitrary_destination_query_param_is_ignored(client, db_session, test_user):
    """The endpoint must never read a caller-supplied destination -- only
    CallSid/To are ever read. Any other phone-shaped query parameter
    (Destination, Number, PhoneNumber, etc) must have zero effect."""
    test_user.phone = "+919812345678"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="HOST")

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
    # Only the real, DB-resolved host number is ever returned -- never one
    # of the attacker-supplied query params above.
    assert _numbers(resp) == ["+919812345678"]


async def test_arbitrary_destination_cannot_override_handoff_routing(client, db_session, test_user):
    test_user.phone = "+919812345678"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="MIRA")
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


# Regression: on_pipeline_finished's real finalize_call_session sequence ------------


async def test_call_finalized_as_completed_by_default_cannot_be_routed(client, db_session, test_user):
    """Baseline for the bug below: finalize_call_session's own default
    (status="completed") is what call_service.py ships with -- confirming
    this refuses to route establishes that the "in_progress" override in
    pipeline.py's on_pipeline_finished (not exercised directly by this
    test file, which only calls the service function, not the full
    pipeline) is load-bearing, not redundant."""
    test_user.phone = "+919812345678"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="MIRA")
    session = await _call_session_for(db_session, property_, handoff_status="requested")

    # Exactly what on_pipeline_finished used to do unconditionally, before
    # the Phase 8 review fix: finalize with no status override, defaulting
    # to "completed".
    await call_service.finalize_call_session(db_session, session.id, transcript="guest: hi\nassistant: hello")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": session.exotel_call_id},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_call_finalized_as_in_progress_for_handoff_can_still_be_routed(client, db_session, test_user):
    """The actual fix, verified end-to-end against the real service
    function and the real endpoint: pipeline.py's on_pipeline_finished now
    calls finalize_call_session(..., status="in_progress") when
    is_host_handoff is True (see the "Phase 8 fix" comment at that call
    site) instead of accepting the "completed" default. This reproduces
    that exact call and confirms connect-routing can still find and route
    the call afterward -- before this fix, this scenario (a real handoff,
    finalized the way on_pipeline_finished actually finalizes it) always
    returned an empty destination, silently breaking live handoff
    end-to-end despite every other Phase 7/8 test passing."""
    test_user.phone = "+919812345678"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="MIRA")
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


# Auth ------------------------------------------------------------------------------


async def test_missing_token_returns_empty(client):
    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"CallSid": "connect_no_token", "To": "+919999999999"},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


async def test_wrong_token_returns_empty(client, db_session, test_user):
    test_user.phone = "+919812345678"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="HOST")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "wrong", "CallSid": "connect_wrong_token", "To": property_.exophone},
    )
    assert resp.status_code == 200
    assert _numbers(resp) == []


# Response shape ----------------------------------------------------------------------


async def test_response_shape_has_no_extra_fields(client, db_session, test_user):
    test_user.phone = "+919812345678"
    db_session.add(test_user)
    await db_session.commit()
    property_ = await _property_with(db_session, test_user, call_handling_mode="HOST")

    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing",
        params={"token": "test-token", "CallSid": "connect_shape", "To": property_.exophone},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"destination"}
    assert set(body["destination"].keys()) == {"numbers"}
