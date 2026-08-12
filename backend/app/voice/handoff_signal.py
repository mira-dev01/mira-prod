"""Phase 7: the in-process signal a live voice pipeline coroutine waits on
to learn "the host has claimed Take Call for THIS call."

Deliberately in-process only, for this phase. app/api/v1/take_call.py's
POST handler (Phase 6) calls request_handoff() right after its atomic
claim succeeds, so the whole claim -> phrase -> teardown path is genuinely
reachable today from a real WhatsApp tap -- but only because this backend
currently runs a single uvicorn worker (confirmed in the architecture-
verification pass earlier this session), so the HTTP request handler and
the live pipeline coroutine are guaranteed to share this same process's
memory. That is an environment fact, not something this module enforces:
making the signal reach a pipeline running in a DIFFERENT process/worker
(Redis pub/sub was the earlier session's own recommendation for that) is
explicitly deferred -- not part of this phase's scope. request_handoff()
already returns False (not an error) when it can't find a registered
call in this process, which is exactly the failure this gap would produce
under multi-process deployment -- see that function's own docstring.

One asyncio.Event per live call, keyed by call_session_id -- a call that
never gets a handoff request never has an entry here at all (no per-call
allocation cost for the overwhelming majority of calls, matching Phase 1's
"NULL means not applicable" discipline for the same feature).
"""

import asyncio
import uuid

_handoff_events: dict[uuid.UUID, asyncio.Event] = {}


def register_call(call_session_id: uuid.UUID) -> None:
    """Called once, at the start of a live call's pipeline, before the
    call could possibly be claimed yet (Take Call only exists once the
    guest_calling WhatsApp notification has gone out, which itself only
    fires after the CallSession already exists -- see app/services/
    guest_calling_notification.py). Idempotent: calling this twice for
    the same call_session_id (should not happen in practice) just resets
    to a fresh, unset Event rather than erroring."""
    _handoff_events[call_session_id] = asyncio.Event()


def unregister_call(call_session_id: uuid.UUID) -> None:
    """Called from the pipeline's own cleanup path, unconditionally --
    every call that registers must also unregister, or this dict grows
    without bound for the life of the process. Safe to call even if
    register_call was never called for this id (e.g. a Lead Agent call,
    which never registers at all -- see pipeline.py's call site)."""
    _handoff_events.pop(call_session_id, None)


def request_handoff(call_session_id: uuid.UUID) -> bool:
    """Called by app/api/v1/take_call.py's POST handler, immediately after
    its own atomic UPDATE ... WHERE handoff_status IS NULL claim succeeds
    (Phase 6) -- the claim itself is already durable in Postgres by the
    time this runs; this is purely "is a live pipeline in THIS process
    listening for it right now." Returns True if so (the common, intended
    case), False if no such call is currently registered here -- either it
    already ended, or (the documented gap, see module docstring) it's
    running in a different process than the one handling this HTTP
    request. A False return is not itself an error signal to take_call.py;
    the claim already succeeded independently of whether a live pipeline
    could actually be reached, and take_call.py's own response to the host
    does not change based on this function's return value."""
    event = _handoff_events.get(call_session_id)
    if event is None:
        return False
    event.set()
    return True


async def wait_for_handoff_request(call_session_id: uuid.UUID) -> None:
    """Blocks until request_handoff(call_session_id) is called, or forever
    if it never is (the overwhelming majority of calls) -- the caller
    (pipeline.py) is expected to run this inside an asyncio.Task alongside
    the rest of the live pipeline, not await it directly, and to cancel
    that task as part of normal call-end cleanup (same shape as the
    existing lease-renewal task, see _renew_call_lease_periodically)."""
    event = _handoff_events.get(call_session_id)
    if event is None:
        # register_call must have already run for this call_session_id --
        # a caller awaiting a handoff for an unregistered call is a bug
        # in the caller, not a state this function should silently
        # tolerate by creating a fresh Event nothing else could ever
        # reach (request_handoff looks up the SAME shared dict; a locally
        # -created Event here would never receive a real signal).
        raise KeyError(f"no registered call_session_id={call_session_id} -- register_call was never invoked")
    await event.wait()
