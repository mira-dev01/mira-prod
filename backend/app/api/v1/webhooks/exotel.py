"""Exotel call-status/passthru callback, (Phase 4) the initial call-
ownership routing Passthru, and (Phase 8) the Connect applet's dynamic
destination lookup.

The actual voice conversation runs through app/api/v1/voice.py (a websocket
the Exotel Voicebot Applet streams audio to). exotel_call_status below only
receives Exotel's own call lifecycle callback (configured as a Passthru/
StatusCallback applet alongside the Voicebot applet in the Exotel call
flow), used for call_sessions logging, recording URLs, and detecting calls
that never reached the AI (busy/no-answer/failed).

exotel_call_routing (Phase 4) is a SEPARATE Passthru applet, placed BEFORE
the Voicebot applet in the account's flow (human-configured in the Exotel
console -- not something this codebase can or does configure). Per Exotel's
documented synchronous-Passthru contract: a GET request, answered
synchronously, whose HTTP status code alone drives which of exactly two
configured branches the flow takes next -- 200 continues to the existing
Voicebot applet (unchanged), 302 continues to a Connect applet (Phase 8
below is what that Connect applet's own dynamic Primary URL now points
at). This endpoint makes ONLY that binary decision; it never dials anyone,
never touches Pipecat/CallCoordinator/Redis, and never creates the
CallSession a real answered call will get once get_or_create_call_session
runs downstream (Voicebot path) or attach_exotel_call runs off the status
callback (either path) -- see PHASE 4's own module-level "Call Session
Correlation" note in the implementation history for why introducing a
second CallSession write path here was deliberately avoided.

exotel_connect_routing (Phase 8) is the Connect applet's own dynamic
Primary URL target -- a THIRD, separate Exotel contract from the two
above: not a binary status-code branch, a JSON body naming the actual PSTN
number to dial. Reached two ways: (1) call_handling_mode=HOST/SCHEDULED
routed a call here directly off exotel_call_routing's 302, before Mira
was ever involved -- no CallSession may exist yet, so this path re-resolves
ownership the same way exotel_call_routing itself did; (2) Phase 7's live
Mira -> host handoff, where a CallSession already exists with
handoff_status="requested" -- this path trusts that durable, atomically-
claimed DB state instead of re-deriving anything. See exotel_connect_
routing's own docstring for exactly how these two are told apart.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.integrations.exotel_client import verify_webhook_token
from app.models.call_session import CallSession
from app.models.property import Property
from app.models.user import User
from app.services import call_ownership, call_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/exotel", tags=["webhooks"])

# Same active-call convention take_call.py (Phase 6) already established --
# _map_exotel_status (call_service.py) only ever writes "in_progress" /
# "completed" / "failed" onto CallSession.status.
_ACTIVE_CALL_STATUSES = {"in_progress"}

# The only handoff_status value that authorizes routing to a host here --
# CallSession.handoff_status's own model comment (app/models/call_session.py)
# documents "requested" (host tapped Take Call, not yet acted on) /
# "connecting" / "connected" / "failed" as the intended future value set;
# nothing but take_call.py's atomic claim writes "requested" today (Phase
# 6/7), and no later value is written by anything in this codebase yet. A
# call sitting in any OTHER non-NULL state is deliberately NOT treated as
# routable here -- see exotel_connect_routing's own docstring.
_ROUTABLE_HANDOFF_STATUS = "requested"


@router.post("/call-status")
async def exotel_call_status(
    request: Request,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not verify_webhook_token(token):
        logger.warning("Rejected Exotel webhook with invalid/missing token")
        return {"error": "unauthorized"}

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
    else:
        data = dict(await request.form())

    call_sid = data.get("CallSid") or data.get("Sid") or data.get("CallSidLeg1")
    if not call_sid:
        logger.warning("Exotel callback missing CallSid: %s", data)
        return {"status": "ignored"}

    await call_service.attach_exotel_call(
        db,
        exotel_call_id=call_sid,
        caller_number=data.get("From"),
        dialed_number=data.get("To"),
        status=data.get("Status") or data.get("DialCallStatus"),
        recording_url=data.get("RecordingUrl"),
    )
    return {"status": "ok"}


@router.get("/call-routing")
async def exotel_call_routing(
    request: Request,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Phase 4's initial call-ownership Passthru -- see the module
    docstring for the full architecture. Fail-closed toward MIRA (HTTP 200)
    on every error path (missing/invalid token, missing CallSid, unknown
    DID, invalid property configuration, resolver error, DB error) -- a
    302 is returned ONLY on an explicit, successful CallOwner.HOST
    resolution. This mirrors CallCoordinator's own documented fail-open
    philosophy (app/services/call_coordinator.py: never let infrastructure
    trouble silently break a live guest call) applied to the one direction
    that matters here -- routing an unknown/misconfigured call to a host
    who never opted in would be the actual incident, not a guest reaching
    Mira when HOST was intended.

    GET, not POST: Exotel's documented synchronous-Passthru contract for a
    binary flow decision is a GET whose response status code alone drives
    the branch -- see this module's docstring. Query params, not a form/
    JSON body like exotel_call_status above (a deliberately different
    shape for a deliberately different Exotel applet type).

    Parameter names are defensive, not confirmed against a live account:
    Exotel's Voice-flow Passthru parameters have not been verified against
    this specific account (see PHASE 4's own report), so both the
    call-status webhook's existing From/To convention and Exotel's other
    documented CallFrom/CallTo naming are accepted -- whichever the account
    actually sends, at least one alias should match. CallSid uses the same
    three-alias fallback exotel_call_status above already relies on.
    """
    if not verify_webhook_token(token):
        logger.warning("Rejected Exotel call-routing request with invalid/missing token")
        return Response(status_code=status.HTTP_200_OK)

    params = request.query_params
    call_sid = params.get("CallSid") or params.get("Sid") or params.get("CallSidLeg1")
    dialed_number = params.get("To") or params.get("CallTo")

    if not call_sid:
        logger.warning("Exotel call-routing request missing CallSid: %s", dict(params))
        return Response(status_code=status.HTTP_200_OK)

    # Property lookup AND ownership resolution share one fail-closed
    # boundary -- a DB error surfacing from get_property_by_number itself
    # (connection drop, timeout) must default to MIRA exactly like a
    # resolver-internal error does; there is no meaningful difference
    # between the two failure sources from a "should this call go to a
    # host who never opted in" standpoint, so they share one except block
    # rather than needing two near-identical ones.
    try:
        property_ = await call_service.get_property_by_number(db, dialed_number)
        if property_ is None:
            # Unknown/unconfigured DID, or a Lead Agent line (host_user_id-
            # only, no single property) -- resolve_effective_call_owner
            # needs a Property row (call_handling_mode lives there, not on
            # User), so a Lead Agent call has no ownership schedule to
            # evaluate at all and falls back to MIRA exactly like every
            # other "can't resolve" branch here. Same DID resolution
            # get_property_by_number already performs for the real
            # Voicebot path (app/voice/pipeline.py) -- not duplicated,
            # reused directly.
            logger.info(
                "Call-routing: no property for dialed_number=%s call_sid=%s -- defaulting to MIRA",
                dialed_number,
                call_sid,
            )
            return Response(status_code=status.HTTP_200_OK)

        owner = call_ownership.resolve_effective_call_owner(property_, datetime.now(timezone.utc))
    except call_ownership.InvalidCallOwnershipConfigError:
        logger.exception(
            "Call-routing: invalid call-ownership configuration for dialed_number=%s call_sid=%s -- "
            "defaulting to MIRA",
            dialed_number,
            call_sid,
        )
        return Response(status_code=status.HTTP_200_OK)
    except Exception:
        # Anything else (a DB error from get_property_by_number itself, an
        # unexpected exception inside the resolver) -- same fail-closed-to-
        # MIRA policy, logged loudly rather than silently swallowed,
        # matching acquire_or_reject's own "call protection DEGRADED"
        # logging discipline (app/services/call_coordinator.py) for the
        # equivalent decision-under-failure case in the existing Mira-side
        # flow.
        logger.exception(
            "Call-routing: unexpected error resolving call owner for dialed_number=%s call_sid=%s -- "
            "defaulting to MIRA",
            dialed_number,
            call_sid,
        )
        return Response(status_code=status.HTTP_200_OK)

    if owner is call_ownership.CallOwner.HOST:
        logger.info("Call-routing: property_id=%s call_sid=%s -> HOST (302)", property_.id, call_sid)
        # Location header content is UNVERIFIED against the live account --
        # per the confirmed account fact this phase was built against,
        # Exotel's synchronous-Passthru branches on the HTTP status code
        # alone (200 vs 302), so the Location value itself should be
        # irrelevant to Exotel's own parsing. Deliberately NOT set to this
        # request's own URL (a self-referential Location would be a real
        # redirect loop if anything other than Exotel -- a proxy, a
        # future curl -L test, a misconfigured client -- ever actually
        # followed it) and NOT set to any fabricated Exotel-internal
        # destination. Omitted entirely: a 302 with no Location is
        # technically non-conformant HTTP, but "no header" is a strictly
        # safer unverified-default than "a header pointing somewhere that
        # might get followed." See PHASE 4's final report for the exact
        # manual Exotel-console test needed to confirm this doesn't need a
        # real value before relying on it in production.
        return Response(status_code=status.HTTP_302_FOUND)

    logger.info("Call-routing: property_id=%s call_sid=%s -> MIRA (200)", property_.id, call_sid)
    return Response(status_code=status.HTTP_200_OK)


def _normalize_destination_phone(value: str | None) -> str | None:
    """Returns a clean, digit-only (plus a leading +) destination number,
    or None if `value` cannot plausibly be one -- and this returned value,
    not the raw `value`, is what the endpoint actually sends back to
    Exotel. Fixed in the Phase 8 review: the original version of this
    function (_is_plausible_phone) only VALIDATED that `value` contained
    enough digits, then the caller sent `value` itself -- the raw,
    unmodified User.phone string -- into the JSON response. User.phone has
    no format validator anywhere in this codebase (app/schemas/user.py's
    PATCH schema accepts any string up to 32 chars), so a host whose phone
    field contains anything beyond a clean number (stray text, punctuation
    a copy-paste left in, e.g. "98765 43210 (call after 6pm)") would have
    that exact garbage string sent to Exotel's Connect API as the dial
    destination -- undefined/unverified behavior on Exotel's side for a
    contract this codebase's own docstring already flags as unconfirmed.
    Confirmed live via direct exploitation of the old function: strings
    with trailing free text passed validation and were returned unchanged.

    Same "no dedicated E.164 validator, don't invent a strict one" stance
    as before -- this still doesn't enforce a specific country format,
    it only strips everything that isn't a digit and requires enough of
    them to plausibly be a real number, same discipline app/utils/phone.py's
    to_india_whatsapp_digits already applies for the WhatsApp send path,
    reused here for the same reason: User.phone is free text, every
    consumer of it in this codebase normalizes before use, never trusts it
    verbatim."""
    if not value or not value.strip():
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) < 10:
        return None
    if not digits.startswith("91") and len(digits) == 10:
        digits = "91" + digits
    return f"+{digits}"


@router.get("/connect-routing")
async def exotel_connect_routing(
    request: Request,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Phase 8: the Exotel Connect applet's own dynamic Primary URL target.
    Reached only after exotel_call_routing (Phase 4) has already returned
    302 for this call, or -- for a live Mira -> host handoff -- after
    Phase 7's pipeline has spoken the handoff phrase and ended the
    Voicebot leg with Exotel's own flow continuing to this same Connect
    applet. This endpoint's only job is answering "which single PSTN
    number should Connect dial for this exact CallSid" -- it never dials
    anyone itself (no outbound Exotel API call), never touches Pipecat/
    CallCoordinator/Redis/WhatsApp/the LLM, and never accepts a
    caller-supplied destination -- see the CORRELATION section of this
    phase's own spec: CallSid is the only identifier trusted, and the
    returned number is always read from User.phone via a DB-verified
    property/host chain, never from a query parameter.

    Fail-closed on every error path: any unresolvable/unauthorized/invalid
    state returns HTTP 200 with an EMPTY numbers list (see _empty_
    destination_response below), never a fabricated or previously-cached
    number and never a 5xx that could itself alter Exotel's own flow
    behavior in an unverified way. An empty numbers list is intentionally
    the single, uniform "cannot safely route this call" signal for every
    failure case, exactly mirroring exotel_call_routing's own "one fail-
    closed outcome for every distinct failure reason" discipline just
    above -- see this phase's own SAFETY section for the full list of
    cases this must cover.

    Two distinct, mutually exclusive ways a call becomes routable here --
    told apart by CallSession lookup, not by any request parameter (a
    request has no way to claim "I am a live handoff" itself):

    1. INITIAL HOST-owned call: call_handling_mode=HOST/SCHEDULED already
       sent this call to Connect via exotel_call_routing's own 302, before
       Mira/Pipecat were ever involved -- get_or_create_call_session has
       not necessarily run yet (that only happens inside the Voicebot
       websocket path, which this call never reaches), so a CallSession
       for this CallSid may not exist. Re-resolves ownership the exact
       same way exotel_call_routing itself just did (dialed number ->
       Property -> resolve_effective_call_owner) and only proceeds on an
       explicit CallOwner.HOST answer -- this is what stops an arbitrary/
       unauthorized call from ever reaching Connect's number lookup: the
       same authorization gate exotel_call_routing already enforced to
       reach this applet at all is re-checked here rather than trusted
       implicitly.
    2. LIVE Mira -> host handoff: a CallSession already exists for this
       CallSid (the call reached the Voicebot, ran through Pipecat) with
       handoff_status=_ROUTABLE_HANDOFF_STATUS ("requested", Phase 6/7's
       atomic claim). Trusts that durable, already-authorized DB state
       directly rather than re-deriving anything -- Phase 6's take_call.py
       already performed the host-authorization check (the token's
       host_user_id/property_id were cross-checked against the DB) at
       claim time, so this only needs to confirm handoff_status remains
       exactly "requested" and the call remains active, not repeat that
       authorization from scratch.

    If a CallSession exists for this CallSid but its handoff_status is
    something OTHER than "requested" (NULL -- never claimed; "connecting"/
    "connected"/"failed" -- a different point in a lifecycle nothing in
    this codebase writes yet), this is treated as an invalid handoff state
    and falls through to the initial-HOST resolution path instead of being
    trusted as a handoff -- a CallSession existing at all does not by
    itself mean "route to host", only a live, freshly-claimed handoff or a
    fresh HOST-mode ownership resolution does.
    """
    if not verify_webhook_token(token):
        logger.warning("Rejected Exotel connect-routing request with invalid/missing token")
        return _empty_destination_response()

    params = request.query_params
    call_sid = params.get("CallSid") or params.get("Sid") or params.get("CallSidLeg1")
    dialed_number = params.get("To") or params.get("CallTo")

    if not call_sid:
        logger.warning("Exotel connect-routing request missing CallSid: %s", dict(params))
        return _empty_destination_response()

    try:
        session = await db.scalar(select(CallSession).where(CallSession.exotel_call_id == call_sid))

        if session is not None and session.handoff_status == _ROUTABLE_HANDOFF_STATUS:
            if session.status not in _ACTIVE_CALL_STATUSES:
                logger.info(
                    "Connect-routing: call_sid=%s has handoff_status=requested but call is no longer "
                    "active (status=%s) -- refusing to route",
                    call_sid,
                    session.status,
                )
                return _empty_destination_response()
            if session.property_id is None:
                logger.warning(
                    "Connect-routing: call_sid=%s handoff has no property_id -- refusing to route", call_sid
                )
                return _empty_destination_response()
            property_ = await db.get(Property, session.property_id)
            return await _resolve_and_respond(db, property_, call_sid, source="handoff")

        # No CallSession, or one exists but isn't in the one routable
        # handoff state -- resolve as an initial HOST-owned call, the exact
        # same authorization exotel_call_routing itself already requires
        # to have reached this applet in the first place.
        property_ = await call_service.get_property_by_number(db, dialed_number)
        if property_ is None:
            logger.info(
                "Connect-routing: no property for dialed_number=%s call_sid=%s -- refusing to route",
                dialed_number,
                call_sid,
            )
            return _empty_destination_response()

        owner = call_ownership.resolve_effective_call_owner(property_, datetime.now(timezone.utc))
        if owner is not call_ownership.CallOwner.HOST:
            logger.warning(
                "Connect-routing: property_id=%s call_sid=%s resolved to MIRA, not HOST -- refusing to "
                "route (unauthorized Connect reach or a schedule boundary race)",
                property_.id,
                call_sid,
            )
            return _empty_destination_response()

        return await _resolve_and_respond(db, property_, call_sid, source="initial-host")
    except call_ownership.InvalidCallOwnershipConfigError:
        logger.exception(
            "Connect-routing: invalid call-ownership configuration for call_sid=%s -- refusing to route",
            call_sid,
        )
        return _empty_destination_response()
    except Exception:
        logger.exception("Connect-routing: unexpected error resolving destination for call_sid=%s", call_sid)
        return _empty_destination_response()


async def _resolve_and_respond(
    db: AsyncSession, property_: Property | None, call_sid: str, *, source: str
) -> Response:
    """Shared tail for both connect-routing paths above once a Property has
    been authorized: Property -> host User -> User.phone, or refuse. Kept
    as one function so both callers apply the exact same host/phone
    validation rather than two near-identical inline copies drifting
    apart."""
    if property_ is None:
        logger.warning("Connect-routing: call_sid=%s property lookup failed -- refusing to route", call_sid)
        return _empty_destination_response()

    host = await db.get(User, property_.user_id)
    if host is None:
        logger.warning(
            "Connect-routing: call_sid=%s property_id=%s has no resolvable host -- refusing to route",
            call_sid,
            property_.id,
        )
        return _empty_destination_response()

    destination_number = _normalize_destination_phone(host.phone)
    if destination_number is None:
        logger.warning(
            "Connect-routing: call_sid=%s host_id=%s has no usable phone -- refusing to route",
            call_sid,
            host.id,
        )
        return _empty_destination_response()

    logger.info(
        "Connect-routing: call_sid=%s property_id=%s host_id=%s -> routing (%s)",
        call_sid,
        property_.id,
        host.id,
        source,
    )
    return _destination_response(destination_number)


def _destination_response(number: str) -> Response:
    return JSONResponse(content={"destination": {"numbers": [number]}})


def _empty_destination_response() -> Response:
    # Fail-closed shape for every error/unauthorized case above: HTTP 200
    # (so Exotel's own Connect applet doesn't treat this as a transport
    # failure and retry/fall back in some unverified way) with an empty
    # numbers list -- never omits the "destination"/"numbers" keys
    # entirely, so a caller parsing this response always finds the same
    # shape whether or not a number was resolved. See this phase's own
    # "Verify exact Exotel response requirements before implementation"
    # note in exotel_connect_routing's docstring: this empty-list shape is
    # this codebase's own safe default, not confirmed against Exotel's
    # actual behavior for a zero-length destination list.
    return JSONResponse(content={"destination": {"numbers": []}})
