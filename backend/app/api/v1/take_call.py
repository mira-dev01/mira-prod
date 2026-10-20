"""Phase 6: the secure Take Call action a host reaches by tapping the
WhatsApp "guest is calling" link (see app/services/guest_calling_
notification.py, Phase 5). Deliberately unauthenticated (no
get_current_user/Clerk session) -- see app/services/take_call_token.py's
own docstring for why a signed, short-lived token is the correct primitive
here instead.

Scope, explicitly: this module claims the handoff (CallSession.handoff_
status: NULL -> "requested"), atomically and exactly once, then (Phase 7)
signals a live pipeline running in THIS process, if one is listening --
see app/voice/handoff_signal.py's own docstring for exactly what that
signal is and its single-process-today limitation. This module still does
NOT perform any telephony action itself and does NOT import app.voice.
pipeline, call_coordinator, or any Exotel/Twilio calling capability --
handoff_signal.py is a tiny, pipeline-adjacent in-process registry, not
telephony control; the actual "speak the handoff phrase, then tear down"
behavior lives entirely in pipeline.py, reacting to this same signal from
its own side.

Two routes, GET then POST, matching the confirmation-before-action pattern
recommended in the prior architecture-verification pass this session
(avoids a link-preview bot or prefetcher silently consuming a GET-only
action link): GET renders a plain-HTML confirmation page (works with no
JS, since this is opened from a mobile WhatsApp browser tab); POST performs
the actual atomic claim, invoked by that page's own form submit.
"""

import logging
from html import escape

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.call_session import CallSession
from app.models.property import Property
from app.services.take_call_token import InvalidTakeCallTokenError, verify_take_call_token
from app.voice import handoff_signal

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/take-call", tags=["take-call"])

_ACTIVE_CALL_STATUSES = {"in_progress"}


def _page(title: str, body: str) -> HTMLResponse:
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)}</title>
  <style>
    :root {{
      --bg: #0f0e0c;
      --fg: #f0ebe3;
      --muted: #8a7e72;
      --coral: #d94f3d;
      --gold: #c9a882;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--fg);
      font-family: -apple-system, "DM Sans", system-ui, sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 32px 24px;
    }}
    .card {{
      max-width: 420px;
      width: 100%;
      text-align: center;
    }}
    .icon {{ font-size: 40px; margin-bottom: 12px; }}
    h1 {{ font-size: 20px; margin: 0 0 8px; }}
    p {{ color: var(--muted); font-size: 15px; line-height: 1.5; margin: 4px 0; }}
    .property {{ color: var(--fg); font-weight: 600; }}
    button {{
      margin-top: 24px;
      background: var(--coral);
      color: var(--fg);
      border: none;
      border-radius: 999px;
      padding: 14px 32px;
      font-size: 16px;
      font-weight: 600;
      cursor: pointer;
    }}
    button:disabled {{ opacity: 0.5; }}
  </style>
</head>
<body>
  <div class="card">
{body}
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


def _invalid_token_page() -> HTMLResponse:
    return _page(
        "Link no longer valid",
        """    <div class="icon">⚠️</div>
    <h1>This link is no longer valid</h1>
    <p>It may have expired or already been used. Check your dashboard for this call's current status.</p>""",
    )


def _call_ended_page() -> HTMLResponse:
    return _page(
        "Call no longer active",
        """    <div class="icon">📴</div>
    <h1>Sorry, this call is no longer active.</h1>
    <p>The guest call has already ended.</p>""",
    )


@router.get("", response_class=HTMLResponse)
async def take_call_confirm(token: str, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    """Renders the confirmation page. Performs NO write -- validating the
    token and checking the call is still active here is read-only, purely
    to give the host an accurate page (correct property name, an honest
    "already ended"/"link expired" message) before they tap anything. The
    actual claim only happens on POST."""
    try:
        call_session_id, property_id, host_user_id = verify_take_call_token(token)
    except InvalidTakeCallTokenError:
        logger.info("take_call_confirm: invalid/expired token")
        return _invalid_token_page()

    session = await db.get(CallSession, call_session_id)
    property_ = await db.get(Property, property_id)

    # Token tampering defense: even a validly-SIGNED token must still refer
    # to a CallSession that actually belongs to the property/host it
    # claims -- this can only diverge if the signing secret itself leaked
    # (verify_take_call_token already rejects a token whose signature
    # doesn't match), but checked anyway rather than trusting the token's
    # own claims as sufficient proof on their own. Same "never trust the
    # token's payload as the final word -- confirm fresh against the DB"
    # discipline the token module's own docstring describes.
    if (
        session is None
        or property_ is None
        or session.property_id != property_id
        or property_.user_id != host_user_id
    ):
        logger.warning(
            "take_call_confirm: token claims do not match DB state (call_session_id=%s property_id=%s "
            "host_user_id=%s)",
            call_session_id,
            property_id,
            host_user_id,
        )
        return _invalid_token_page()

    if session.status not in _ACTIVE_CALL_STATUSES:
        return _call_ended_page()

    if session.handoff_status is not None:
        return _page(
            "Already requested",
            f"""    <div class="icon">✅</div>
    <h1>Call takeover already requested.</h1>
    <p class="property">{escape(_property_label(property_))}</p>
    <p>Guest: {escape(session.caller_number or "Unknown number")}</p>""",
        )

    guest_label = escape(session.caller_number or "Unknown number")
    property_label = escape(_property_label(property_))
    return _page(
        "Guest is calling",
        f"""    <div class="icon">📞</div>
    <h1>Guest is calling</h1>
    <p class="property">{property_label}</p>
    <p>Guest: {guest_label}</p>
    <p>Mira is currently handling this call.</p>
    <form method="post" action="/api/v1/take-call?token={escape(token)}">
      <button type="submit">Take Call</button>
    </form>""",
    )


@router.post("", response_class=HTMLResponse)
async def take_call_claim(token: str, db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    """The atomic claim. One UPDATE ... WHERE handoff_status IS NULL,
    exactly the same shape as recovery_service.py's _claim_lead (this
    codebase's own established atomic-claim precedent) -- Postgres only
    ever matches/updates a row for whichever request's UPDATE commits
    first; a second concurrent request against the same call_session_id
    simply matches zero rows once the first has committed the NULL ->
    "requested" flip, with no window where both could see it as
    claimable. No new locking primitive, no Redis -- the same guarantee
    class as call_coordinator.py's Lua compare-and-swap, via Postgres row-
    level locking instead."""
    try:
        call_session_id, property_id, host_user_id = verify_take_call_token(token)
    except InvalidTakeCallTokenError:
        logger.info("take_call_claim: invalid/expired token")
        return _invalid_token_page()

    session = await db.get(CallSession, call_session_id)
    property_ = await db.get(Property, property_id)

    if (
        session is None
        or property_ is None
        or session.property_id != property_id
        or property_.user_id != host_user_id
    ):
        logger.warning(
            "take_call_claim: token claims do not match DB state (call_session_id=%s property_id=%s "
            "host_user_id=%s)",
            call_session_id,
            property_id,
            host_user_id,
        )
        return _invalid_token_page()

    if session.status not in _ACTIVE_CALL_STATUSES:
        return _call_ended_page()

    # TOCTOU fix: session.status above was read moments earlier via a
    # plain db.get() -- the call could legitimately end (on_pipeline_
    # finished -> finalize_call_session, a genuinely independent/
    # concurrent code path) in the window between that read and this
    # UPDATE actually committing. The WHERE clause below re-checks
    # status_in the SAME atomic statement as the handoff_status flip, not
    # as a separate earlier read, so a call that ends in that window is
    # caught here too, not just by the (now redundant but harmless) check
    # above. Both conditions must hold atomically for the claim to
    # succeed -- exactly the same "no window where two things could both
    # see it as claimable" guarantee recovery_service._claim_lead already
    # established for its own WHERE clause.
    stmt = (
        update(CallSession)
        .where(
            CallSession.id == call_session_id,
            CallSession.handoff_status.is_(None),
            CallSession.status.in_(_ACTIVE_CALL_STATUSES),
        )
        .values(handoff_status="requested")
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    await db.commit()
    await db.refresh(session)

    property_label = escape(_property_label(property_))
    if result.rowcount == 0:
        # Lost the race (another request/device claimed it first), the
        # call ended in the window described above, or a non-NULL
        # handoff_status already existed for some other reason -- any of
        # these is a safe no-op response, never a second telephony action
        # attempted. Distinguish "ended" from "already claimed" using the
        # freshly-refreshed session, so a call that ended in that narrow
        # window gets the accurate "no longer active" message instead of
        # a misleading "already requested".
        if session.status not in _ACTIVE_CALL_STATUSES:
            logger.info("take_call_claim: call ended during claim attempt for call_session_id=%s", call_session_id)
            return _call_ended_page()
        logger.info(
            "take_call_claim: lost claim race or already claimed for call_session_id=%s", call_session_id
        )
        return _page(
            "Already requested",
            f"""    <div class="icon">✅</div>
    <h1>Call takeover already requested.</h1>
    <p class="property">{property_label}</p>""",
        )

    logger.info(
        "take_call_claim: handoff requested for call_session_id=%s property_id=%s host_user_id=%s",
        call_session_id,
        property_id,
        host_user_id,
    )
    # Phase 7: signal the live pipeline, if one is listening in THIS
    # process (see app/voice/handoff_signal.py's own docstring for the
    # single-process-today caveat). The DB claim above is already durable
    # and authoritative regardless of this call's outcome -- a False
    # return (no live pipeline registered here) does not change this
    # response or roll back the claim; it just means Phase 7's
    # speak-then-teardown behavior won't fire for this particular call
    # (e.g. it's already ended by the time this races through, or a
    # future multi-process deployment routed this request to a different
    # worker than the one running the call).
    handoff_signal.request_handoff(call_session_id)
    # Deliberately does NOT say "this page will update" or anything
    # implying a live status change is coming -- this response is static
    # HTML with no polling/refresh/WebSocket, so it would never actually
    # update on its own (the earlier draft of this copy made exactly that
    # false claim, fixed in review). Phase 6 has not connected the guest
    # and host yet -- only flipped handoff_status to "requested" -- so the
    # copy must not imply the takeover has happened or is progressing on
    # this screen; it hasn't, and this page has no way to know if/when it
    # does.
    return _page(
        "Call takeover requested",
        f"""    <div class="icon">✅</div>
    <h1>Call takeover requested.</h1>
    <p class="property">{property_label}</p>
    <p>Mira has been notified.</p>""",
    )


def _property_label(property_: Property) -> str:
    return property_.spoken_name or property_.display_name or property_.name
