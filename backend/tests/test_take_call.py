"""Phase 6: GET/POST /api/v1/take-call -- the secure Take Call claim.
Every test drives the real HTTP endpoints via the shared `client` fixture
(no direct function calls), since the whole point of this phase is the
request-level security contract (token verification, DB-state
cross-checks, atomic claim). No telephony assertions anywhere -- Phase 6
only flips CallSession.handoff_status."""

import uuid
from datetime import timedelta

import jwt
import pytest

from app.config import settings
from app.models.call_session import CallSession
from app.models.property import Property
from app.models.user import User
from app.services.take_call_token import TOKEN_TTL, issue_take_call_token


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


# 1. Valid token -----------------------------------------------------------------------


async def test_valid_token_confirm_page_shows_property_and_guest(client, db_session, test_property):
    session = await _call_session_for(db_session, test_property)
    token = issue_take_call_token(session.id, test_property.id, test_property.user_id)

    resp = await client.get("/api/v1/take-call", params={"token": token})
    assert resp.status_code == 200
    assert "Take Call" in resp.text
    assert test_property.name in resp.text
    assert "+919999999999" in resp.text


async def test_valid_token_claim_succeeds(client, db_session, test_property):
    session = await _call_session_for(db_session, test_property)
    token = issue_take_call_token(session.id, test_property.id, test_property.user_id)

    resp = await client.post("/api/v1/take-call", params={"token": token})
    assert resp.status_code == 200
    assert "Call takeover requested" in resp.text

    await db_session.refresh(session)
    assert session.handoff_status == "requested"


async def test_success_page_copy_does_not_overclaim_connection_or_live_update(client, db_session, test_property):
    """Phase 6 only flips handoff_status to "requested" -- it must never
    say or imply the guest and host are actually connected/transferred
    (that's Phase 7), and must never claim this static HTML page will
    "update" on its own (it has no polling/refresh/WebSocket -- an
    earlier draft's copy made exactly that false promise, caught in
    review)."""
    session = await _call_session_for(db_session, test_property)
    token = issue_take_call_token(session.id, test_property.id, test_property.user_id)

    resp = await client.post("/api/v1/take-call", params={"token": token})
    text_lower = resp.text.lower()
    assert "requested" in text_lower
    for overclaim in ("connected", "transferred", "will update", "joined", "now live"):
        assert overclaim not in text_lower, f"success page must not claim: {overclaim!r}"


# 2. Expired token -----------------------------------------------------------------------


async def test_expired_token_confirm_page_rejects(client, db_session, test_property):
    session = await _call_session_for(db_session, test_property)
    # Mint with a negative TTL by hand -- issue_take_call_token itself
    # always uses TOKEN_TTL, so construct an already-expired token directly
    # via the same signing mechanism to exercise the expiry check.
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    payload = {
        "call_session_id": str(session.id),
        "property_id": str(test_property.id),
        "host_user_id": str(test_property.user_id),
        "iat": now - TOKEN_TTL - timedelta(minutes=1),
        "exp": now - timedelta(minutes=1),
    }
    expired_token = jwt.encode(payload, settings.take_call_token_secret, algorithm="HS256")

    resp = await client.get("/api/v1/take-call", params={"token": expired_token})
    assert resp.status_code == 200
    assert "no longer valid" in resp.text.lower()


async def test_expired_token_claim_rejects(client, db_session, test_property):
    session = await _call_session_for(db_session, test_property)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    payload = {
        "call_session_id": str(session.id),
        "property_id": str(test_property.id),
        "host_user_id": str(test_property.user_id),
        "iat": now - TOKEN_TTL - timedelta(minutes=1),
        "exp": now - timedelta(minutes=1),
    }
    expired_token = jwt.encode(payload, settings.take_call_token_secret, algorithm="HS256")

    resp = await client.post("/api/v1/take-call", params={"token": expired_token})
    assert resp.status_code == 200
    assert "no longer valid" in resp.text.lower()

    await db_session.refresh(session)
    assert session.handoff_status is None


# 3. Tampered token ------------------------------------------------------------------------


async def test_tampered_token_signature_rejects(client, db_session, test_property):
    session = await _call_session_for(db_session, test_property)
    token = issue_take_call_token(session.id, test_property.id, test_property.user_id)
    # Flip a character in the middle of the signature, not the last one: a
    # base64url-encoded 32-byte HMAC-SHA256 signature's final character only
    # encodes 2 bits (32 bytes -> 43 base64 chars with padding), so flipping
    # *only* that last character has a real ~1-in-4 chance of round-tripping
    # to the exact same signature bytes -- confirmed by direct reproduction,
    # which intermittently made this "tampered" token verify successfully.
    header, payload, signature = token.split(".")
    mid = len(signature) // 2
    tampered_char = "A" if signature[mid] != "A" else "B"
    tampered_signature = signature[:mid] + tampered_char + signature[mid + 1 :]
    tampered = f"{header}.{payload}.{tampered_signature}"

    resp = await client.post("/api/v1/take-call", params={"token": tampered})
    assert resp.status_code == 200
    assert "no longer valid" in resp.text.lower()

    await db_session.refresh(session)
    assert session.handoff_status is None


async def test_token_signed_with_wrong_secret_rejects(client, db_session, test_property):
    session = await _call_session_for(db_session, test_property)
    forged = jwt.encode(
        {
            "call_session_id": str(session.id),
            "property_id": str(test_property.id),
            "host_user_id": str(test_property.user_id),
        },
        "not-the-real-secret",
        algorithm="HS256",
    )

    resp = await client.post("/api/v1/take-call", params={"token": forged})
    assert resp.status_code == 200
    assert "no longer valid" in resp.text.lower()


# 4. Wrong property ------------------------------------------------------------------------


async def test_token_property_mismatch_rejects(client, db_session, test_property, test_user):
    other_property = Property(
        user_id=test_user.id,
        name="Other Villa",
        base_price=1000,
        exophone=f"+9180{uuid.uuid4().int % 10**8:08d}",
    )
    db_session.add(other_property)
    await db_session.commit()
    await db_session.refresh(other_property)

    session = await _call_session_for(db_session, test_property)
    # Token claims a DIFFERENT property than the one the CallSession
    # actually belongs to.
    token = issue_take_call_token(session.id, other_property.id, other_property.user_id)

    resp = await client.post("/api/v1/take-call", params={"token": token})
    assert resp.status_code == 200
    assert "no longer valid" in resp.text.lower()

    await db_session.refresh(session)
    assert session.handoff_status is None


# 5. Wrong host --------------------------------------------------------------------------


async def test_token_host_mismatch_rejects(client, db_session, test_property):
    other_host = User(
        email=f"other-{uuid.uuid4().hex[:8]}@example.com",
        clerk_user_id=f"user_{uuid.uuid4().hex[:16]}",
        name="Other Host",
    )
    db_session.add(other_host)
    await db_session.commit()
    await db_session.refresh(other_host)

    session = await _call_session_for(db_session, test_property)
    # property_id is real, but host_user_id doesn't match property_.user_id.
    token = issue_take_call_token(session.id, test_property.id, other_host.id)

    resp = await client.post("/api/v1/take-call", params={"token": token})
    assert resp.status_code == 200
    assert "no longer valid" in resp.text.lower()

    await db_session.refresh(session)
    assert session.handoff_status is None


# 6. Missing call --------------------------------------------------------------------------


async def test_unknown_call_session_rejects(client, test_property):
    token = issue_take_call_token(uuid.uuid4(), test_property.id, test_property.user_id)

    resp = await client.post("/api/v1/take-call", params={"token": token})
    assert resp.status_code == 200
    assert "no longer valid" in resp.text.lower()


# 7. Ended call ----------------------------------------------------------------------------


async def test_ended_call_confirm_shows_no_longer_active(client, db_session, test_property):
    session = await _call_session_for(db_session, test_property, status="completed")
    token = issue_take_call_token(session.id, test_property.id, test_property.user_id)

    resp = await client.get("/api/v1/take-call", params={"token": token})
    assert resp.status_code == 200
    assert "no longer active" in resp.text.lower()


async def test_ended_call_claim_rejects_without_telephony_attempt(client, db_session, test_property):
    session = await _call_session_for(db_session, test_property, status="completed")
    token = issue_take_call_token(session.id, test_property.id, test_property.user_id)

    resp = await client.post("/api/v1/take-call", params={"token": token})
    assert resp.status_code == 200
    assert "no longer active" in resp.text.lower()

    await db_session.refresh(session)
    # Never claimed -- an ended call must not transition handoff_status at
    # all, not even to "requested".
    assert session.handoff_status is None


async def test_call_ending_between_status_read_and_atomic_update_still_cannot_be_claimed(
    client, db_session, test_property, monkeypatch
):
    """Regression: the initial `session.status not in _ACTIVE_CALL_STATUSES`
    check in take_call_claim reads status via a plain db.get() BEFORE the
    atomic UPDATE runs -- a genuinely concurrent call-completion
    (on_pipeline_finished -> finalize_call_session, an independent code
    path) landing in that exact window would previously have let the claim
    still succeed, because the UPDATE's own WHERE clause only checked
    handoff_status, not status. Fixed by folding status into the same
    atomic WHERE clause. This test forces the race deterministically: the
    call is flipped to "completed" via a SEPARATE session, between the
    request handler's initial read and its UPDATE, by monkeypatching
    AsyncSession.execute to mutate the row (through a fresh connection)
    the first time it's called for anything other than the initial
    db.get() lookups."""
    from sqlalchemy import update as sa_update
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.database import AsyncSessionLocal
    from app.models.call_session import CallSession as CallSessionModel

    session = await _call_session_for(db_session, test_property, status="in_progress")
    token = issue_take_call_token(session.id, test_property.id, test_property.user_id)

    original_execute = AsyncSession.execute
    raced = {"done": False}

    async def _execute_with_race(self, *args, **kwargs):
        # Only interpose on the first genuine UPDATE this request issues
        # (the take_call_claim's own atomic claim) -- db.get() calls don't
        # route through .execute() the same way, so this reliably fires
        # exactly once, right before the real claim statement runs.
        if not raced["done"]:
            raced["done"] = True
            async with AsyncSessionLocal() as racer_db:
                await racer_db.execute(
                    sa_update(CallSessionModel)
                    .where(CallSessionModel.id == session.id)
                    .values(status="completed")
                )
                await racer_db.commit()
        return await original_execute(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", _execute_with_race)

    resp = await client.post("/api/v1/take-call", params={"token": token})
    assert resp.status_code == 200
    assert "no longer active" in resp.text.lower()
    assert "already requested" not in resp.text.lower()

    await db_session.refresh(session)
    assert session.handoff_status is None
    assert session.status == "completed"


# 8. Duplicate claim -----------------------------------------------------------------------


async def test_duplicate_claim_is_a_safe_no_op(client, db_session, test_property):
    session = await _call_session_for(db_session, test_property)
    token = issue_take_call_token(session.id, test_property.id, test_property.user_id)

    first = await client.post("/api/v1/take-call", params={"token": token})
    assert first.status_code == 200
    assert "Call takeover requested" in first.text

    second = await client.post("/api/v1/take-call", params={"token": token})
    assert second.status_code == 200
    assert "already requested" in second.text.lower()

    await db_session.refresh(session)
    assert session.handoff_status == "requested"


async def test_confirm_page_after_claim_shows_already_requested(client, db_session, test_property):
    session = await _call_session_for(db_session, test_property)
    token = issue_take_call_token(session.id, test_property.id, test_property.user_id)
    await client.post("/api/v1/take-call", params={"token": token})

    resp = await client.get("/api/v1/take-call", params={"token": token})
    assert resp.status_code == 200
    assert "already requested" in resp.text.lower()


# 9. Concurrent claim ----------------------------------------------------------------------


async def test_concurrent_claims_only_one_succeeds(client, db_session, test_property):
    """Not literally simultaneous (the test client is sequential), but
    proves the underlying atomic UPDATE ... WHERE handoff_status IS NULL
    is what actually decides the winner -- a second claim attempt against
    an already-"requested" row deterministically matches zero rows,
    exactly the same guarantee that would hold under real concurrency
    (Postgres row-level locking on the UPDATE, not a race the caller could
    ever observe as "both won"). Fires several rapid claims to make sure
    none of them silently succeeds a second time."""
    session = await _call_session_for(db_session, test_property)
    token = issue_take_call_token(session.id, test_property.id, test_property.user_id)

    responses = [await client.post("/api/v1/take-call", params={"token": token}) for _ in range(4)]
    assert all(r.status_code == 200 for r in responses)

    successes = [r for r in responses if "Call takeover requested." in r.text]
    already_claimed = [r for r in responses if "already requested" in r.text.lower()]
    assert len(successes) == 1
    assert len(already_claimed) == 3

    await db_session.refresh(session)
    assert session.handoff_status == "requested"


# 10. Malformed token ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_token",
    [
        "not-a-jwt-at-all",
        "",
        "a.b.c.d.e",
    ],
)
async def test_malformed_token_rejects(client, bad_token):
    resp = await client.post("/api/v1/take-call", params={"token": bad_token})
    assert resp.status_code == 200
    assert "no longer valid" in resp.text.lower()


async def test_token_missing_required_claim_rejects(client, db_session, test_property):
    session = await _call_session_for(db_session, test_property)
    # Valid signature, but missing property_id/host_user_id entirely.
    incomplete = jwt.encode(
        {"call_session_id": str(session.id)}, settings.take_call_token_secret, algorithm="HS256"
    )

    resp = await client.post("/api/v1/take-call", params={"token": incomplete})
    assert resp.status_code == 200
    assert "no longer valid" in resp.text.lower()


async def test_token_with_valid_signature_but_no_exp_claim_never_bypasses_expiry(
    client, db_session, test_property
):
    """Regression: pyjwt's verify_exp only checks the exp claim IF
    PRESENT -- it does not, by itself, require a token to carry one.
    Confirmed live before the fix: a validly-signed token with every
    other claim correct but no `exp` at all decoded successfully and
    would never expire. options={"require": [...]} in
    verify_take_call_token now makes exp (and every other required
    claim) mandatory for verification to succeed at all, regardless of
    signature validity."""
    session = await _call_session_for(db_session, test_property)
    no_exp_token = jwt.encode(
        {
            "call_session_id": str(session.id),
            "property_id": str(test_property.id),
            "host_user_id": str(test_property.user_id),
            # Deliberately no "exp" claim, real secret, real algorithm.
        },
        settings.take_call_token_secret,
        algorithm="HS256",
    )

    resp = await client.post("/api/v1/take-call", params={"token": no_exp_token})
    assert resp.status_code == 200
    assert "no longer valid" in resp.text.lower()

    await db_session.refresh(session)
    assert session.handoff_status is None


async def test_alg_none_forged_token_is_rejected(client, db_session, test_property):
    """Regression/defense-in-depth: the classic JWT alg=none forgery --
    a token claiming an unsigned "none" algorithm, which some naive
    verifiers accept because there's technically a valid (empty)
    signature for that "algorithm". verify_take_call_token pins
    algorithms=["HS256"] explicitly (never reads the algorithm from the
    token's own header), so this must be rejected regardless of what the
    forged token's header claims."""
    session = await _call_session_for(db_session, test_property)
    forged = jwt.encode(
        {
            "call_session_id": str(session.id),
            "property_id": str(test_property.id),
            "host_user_id": str(test_property.user_id),
        },
        key="",
        algorithm="none",
    )

    resp = await client.post("/api/v1/take-call", params={"token": forged})
    assert resp.status_code == 200
    assert "no longer valid" in resp.text.lower()

    await db_session.refresh(session)
    assert session.handoff_status is None


async def test_token_with_non_uuid_claim_rejects(client):
    bad_uuid_token = jwt.encode(
        {
            "call_session_id": "not-a-uuid",
            "property_id": str(uuid.uuid4()),
            "host_user_id": str(uuid.uuid4()),
        },
        settings.take_call_token_secret,
        algorithm="HS256",
    )
    resp = await client.post("/api/v1/take-call", params={"token": bad_uuid_token})
    assert resp.status_code == 200
    assert "no longer valid" in resp.text.lower()


# No auth session required (deliberately unauthenticated flow) -----------------------------


async def test_take_call_requires_no_clerk_session(client, db_session, test_property):
    """Confirms this endpoint is genuinely reachable with NO Authorization
    header at all -- the whole point of a signed action token instead of
    get_current_user."""
    session = await _call_session_for(db_session, test_property)
    token = issue_take_call_token(session.id, test_property.id, test_property.user_id)

    resp = await client.get("/api/v1/take-call", params={"token": token})
    assert resp.status_code == 200
    assert "Take Call" in resp.text
