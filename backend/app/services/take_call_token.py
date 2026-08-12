"""Phase 6: the signed action token embedded in a Take Call WhatsApp link.

A host taps this link from WhatsApp with no Clerk session at all -- this is
deliberately NOT app/auth/dependencies.py's get_current_user (a full Clerk
RS256 session token). Confirmed in the prior architecture-verification pass
(this session) that no scoped, short-lived, single-use, signed token
pattern already exists in this codebase to reuse -- the closest precedents
are Clerk's own JWT (wrong shape: a full login session, not an ephemeral
action grant) and the static, non-expiring shared-secret webhook token
(app/utils/webhook_auth.py: authenticates "is this really Exotel/Twilio,"
never "is this a specific one-time action for a specific call"). This is a
new, narrow primitive -- signs with pyjwt (already a dependency, used today
only for *verifying* Clerk's tokens; HS256 signing with an app-owned secret
needs no new package).

The token itself only ever needs to prove two things: (1) it was minted by
us for this specific call_session_id/property_id/host_user_id triple, and
(2) it hasn't expired. It does NOT carry a phone number, a destination
number, or any secret -- those are looked up fresh from the DB after
verification, never trusted from the token payload (see take_call.py's own
validation chain). A forged/tampered token fails signature verification
before any of its claims are ever read.
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.config import settings

# Generous enough that a host opening WhatsApp and tapping the link within
# a normal live-call timeframe never sees a false "expired" -- short enough
# that a link sitting unread in a chat thread for hours/days is no longer
# usable once the guest call it refers to has almost certainly ended
# anyway. Not tied to any particular call-duration assumption; this is
# purely a "how long is this specific grant valid for" bound, independent
# of whatever the CallSession's own active/ended state later confirms.
TOKEN_TTL = timedelta(minutes=10)

_ALGORITHM = "HS256"


class InvalidTakeCallTokenError(Exception):
    """Raised for any token that fails to verify -- expired, tampered
    (bad signature), or malformed (not a JWT at all, missing a required
    claim). Deliberately one exception type for all three: the caller
    (take_call.py) responds identically to each case (a generic "this link
    is no longer valid" page), so there is no reason to give an attacker
    a more specific signal about which failure mode triggered."""


def issue_take_call_token(
    call_session_id: uuid.UUID, property_id: uuid.UUID, host_user_id: uuid.UUID
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "call_session_id": str(call_session_id),
        "property_id": str(property_id),
        "host_user_id": str(host_user_id),
        "iat": now,
        "exp": now + TOKEN_TTL,
    }
    return jwt.encode(payload, settings.take_call_token_secret, algorithm=_ALGORITHM)


def verify_take_call_token(token: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (call_session_id, property_id, host_user_id) on success.
    Raises InvalidTakeCallTokenError on any failure -- expired, bad
    signature, or a structurally malformed/incomplete payload."""
    try:
        # options={"require": ["exp"]}: pyjwt's verify_exp only validates
        # the exp claim IF PRESENT -- it does not, by itself, reject a
        # token that omits exp entirely (confirmed: a hand-crafted token
        # signed with the real secret but no exp claim decodes
        # successfully and never expires). require=["exp"] closes that
        # gap explicitly, so "this token type always expires" is an
        # enforced property of verification itself, not something that
        # merely happens to hold because issue_take_call_token always sets
        # it. algorithms=[_ALGORITHM] (not read from the token's own
        # header) blocks the classic alg=none/algorithm-confusion forgery
        # class -- confirmed live against an alg=none-signed token.
        payload = jwt.decode(
            token,
            settings.take_call_token_secret,
            algorithms=[_ALGORITHM],
            options={"require": ["exp", "call_session_id", "property_id", "host_user_id"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTakeCallTokenError("token failed verification") from exc

    try:
        call_session_id = uuid.UUID(payload["call_session_id"])
        property_id = uuid.UUID(payload["property_id"])
        host_user_id = uuid.UUID(payload["host_user_id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidTakeCallTokenError("token payload missing/malformed required claims") from exc

    return call_session_id, property_id, host_user_id
