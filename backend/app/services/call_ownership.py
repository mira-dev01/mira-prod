"""resolve_effective_call_owner: the one pure domain decision Phase 1's Call
Ownership Schedule data model exists to support -- "given this property's
configuration and the current UTC time, who owns an inbound guest call right
now: HOST or MIRA."

Deliberately its own module rather than folded into app/services/
call_service.py (which owns DID -> Property/User resolution, a related but
distinct concern -- DB-backed CRUD/lookup helpers, not domain math) or
app/utils/ (this has real branching logic and its own exception type,
unlike the trivial one-line helpers living there today, e.g.
app/utils/phone.py).

Purity is the whole point of this module: no DB session, no Redis, no HTTP,
no logging, no mutation, no global/module-level state. Same
(property, current_time_utc) in, same CallOwner out, every time -- this is
what lets Phase 4's actual call-arrival routing (not built yet) call it
inline, synchronously, on every single inbound call with zero I/O cost.
Nothing in this codebase calls this function yet -- see the module docstring
warning below and this phase's own scope note: wiring it into
app/voice/pipeline.py, the Exotel webhook, or CallCoordinator is explicitly
Phase 4's job, not this one's.
"""

import enum
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models.property import Property


class CallOwner(enum.Enum):
    """The resolver's only two possible answers. Deliberately not reusing
    Property.call_handling_mode's three-value string space (MIRA/HOST/
    SCHEDULED) for the return type -- SCHEDULED is a *configuration*, never
    an *answer*: the whole point of this function is collapsing SCHEDULED
    down to a concrete HOST or MIRA for one instant in time. A caller
    receiving CallOwner.SCHEDULED back would be a bug, not a valid state,
    so it's not a member of this enum at all.

    A real enum.Enum, not a plain string constant pair, so a typo like
    CallOwner.MRIA fails at attribute-access time rather than producing a
    silently-wrong string a future routing branch would never match
    against "MIRA"/"HOST" -- same reasoning, and the same plain
    enum.Enum(no str mixin) shape, as this codebase's one other enum,
    app/services/call_coordinator.py's Decision ("the only two outcomes
    acquire_or_reject() can hand back... nothing else for a caller to
    branch on"). Every model column still deliberately uses a plain string
    instead (see Property.call_handling_mode's own comment) -- this enum
    is scoped tightly to this function's in-memory return value only,
    never persisted, so it doesn't collide with or duplicate that
    convention.
    """

    HOST = "HOST"
    MIRA = "MIRA"


class InvalidCallOwnershipConfigError(ValueError):
    """Raised when a property's stored call-ownership configuration cannot
    be resolved -- SCHEDULED mode missing a required schedule bound, an
    unparseable HH:MM string, or a timezone identifier ZoneInfo doesn't
    recognize. Mirrors NegotiationPolicyParseError's precedent (app/
    services/negotiation_policy_service.py): a plain, locally-scoped
    exception, and the same "callers must not silently fall back" contract
    -- this resolver never guesses a default HOST/MIRA answer or a default
    timezone on bad input, it raises. In practice this should be
    unreachable in production: app/schemas/property.py's PropertyUpdate
    validators (Phase 1) already reject an invalid mode/time-format/
    timezone/incomplete-SCHEDULED combination before a row can ever be
    saved via the API. This exists as a defensive contract for this
    function's own callers, not because bad rows are expected to exist --
    e.g. a property row written directly against the DB, bypassing the API
    validators entirely, or a future caller that constructs a Property
    object in memory without going through PropertyUpdate at all.
    """


def _parse_hh_mm(value: str, *, field_name: str) -> time:
    # value is None or not a string: caught here explicitly, not left to
    # AttributeError from calling .split() on it -- resolve_effective_call_
    # owner already guards against None before ever reaching this function
    # (see its own None check on start_raw/end_raw), but this function has
    # no way to enforce that invariant on a caller who invokes it directly,
    # so it validates its own input rather than trusting the guard above it
    # to always run first.
    if not isinstance(value, str):
        raise InvalidCallOwnershipConfigError(f"{field_name}={value!r} is not a valid HH:MM 24-hour time string")
    try:
        hour_str, minute_str = value.split(":", 1)
        return time(hour=int(hour_str), minute=int(minute_str))
    except ValueError as exc:
        raise InvalidCallOwnershipConfigError(
            f"{field_name}={value!r} is not a valid HH:MM 24-hour time string"
        ) from exc


def resolve_effective_call_owner(property_: Property, current_time_utc: datetime) -> CallOwner:
    """Who owns an inbound call for `property_` at `current_time_utc`.

    `current_time_utc` must be a timezone-aware datetime (any aware
    timezone is accepted and converted internally -- it does not need to
    already be UTC specifically, since .astimezone() handles the
    conversion -- but callers should pass real UTC, per this function's
    contract with its future caller: the pipeline resolves "now" once, in
    UTC, at call arrival). A naive datetime raises InvalidCallOwnershipConfigError
    rather than silently being treated as UTC -- guessing a naive
    datetime's timezone is exactly the kind of silent assumption this
    function's whole design (see the module docstring, and every "do not
    silently..." rule in this function) refuses to make.

    MIRA / HOST modes are unconditional -- schedule/timezone fields are
    never read for these, even if populated (matches Phase 1's own model
    comment: "Meaningless/ignored when call_handling_mode is MIRA or
    HOST"). SCHEDULED mode requires both schedule bounds and a valid
    timezone to be set; see InvalidCallOwnershipConfigError's docstring for
    why a missing/invalid one raises instead of guessing.
    """
    if current_time_utc.tzinfo is None:
        raise InvalidCallOwnershipConfigError(
            "current_time_utc must be timezone-aware; a naive datetime cannot be safely assumed to be UTC"
        )

    mode = property_.call_handling_mode
    if mode == "MIRA":
        return CallOwner.MIRA
    if mode == "HOST":
        return CallOwner.HOST
    if mode != "SCHEDULED":
        raise InvalidCallOwnershipConfigError(f"call_handling_mode={mode!r} is not one of MIRA, HOST, SCHEDULED")

    start_raw = property_.call_handling_schedule_start
    end_raw = property_.call_handling_schedule_end
    if start_raw is None or end_raw is None:
        raise InvalidCallOwnershipConfigError(
            "call_handling_mode=SCHEDULED requires both call_handling_schedule_start and "
            "call_handling_schedule_end to be set"
        )

    tz_name = property_.timezone
    if not tz_name:
        # Property.timezone has a non-null DB default ("Asia/Kolkata"), so
        # this should be unreachable for any row that went through a real
        # migration/insert -- guarded anyway rather than letting
        # ZoneInfo(None) raise its own less-specific TypeError, keeping
        # every failure mode in this function surfaced as the same
        # exception type.
        raise InvalidCallOwnershipConfigError("call_handling_mode=SCHEDULED requires a property timezone to be set")

    try:
        zone = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise InvalidCallOwnershipConfigError(f"timezone={tz_name!r} is not a valid IANA timezone identifier") from exc

    local_time = current_time_utc.astimezone(zone).time()
    start = _parse_hh_mm(start_raw, field_name="call_handling_schedule_start")
    end = _parse_hh_mm(end_raw, field_name="call_handling_schedule_end")

    in_host_hours = _time_in_half_open_interval(local_time, start, end)
    return CallOwner.HOST if in_host_hours else CallOwner.MIRA


def _time_in_half_open_interval(value: time, start: time, end: time) -> bool:
    """Half-open [start, end) wall-clock membership, handling both same-day
    windows (start < end, e.g. 09:00-17:00) and overnight windows that wrap
    past midnight (start > end, e.g. 22:00-06:00) with the same comparison
    -- no date arithmetic, no separate branch keyed on which case it is,
    matching this feature's data model, which stores neither a date nor a
    day-of-week (see Property.call_handling_schedule_start/_end's own
    comment).

    start == end is the one genuinely ambiguous case the product spec
    doesn't name explicitly (its own worked overnight example never has
    them equal, and the [start, end) half-open framing gives a precise
    answer once you take it literally). Interpreted as an EMPTY interval,
    matching what [start, end) already means for start == end in any
    half-open-interval convention: HOST HOURS never actually occur, so
    every call resolves to MIRA. Chosen over the other plausible reading
    ("start == end means HOST all day, a full 24h wrap") because it
    requires no special-casing at all here -- both branches below already
    produce this result for start == end without being told to -- and
    because "the host configured a zero-width window" reads far more like
    a host who hasn't finished configuring their schedule yet than one who
    deliberately wants Mira fully disabled, which is exactly what plain
    HOST mode (no schedule involved) already exists to express
    unambiguously. Explicitly tested in
    test_call_ownership.py::test_equal_start_and_end_is_always_mira.
    """
    if start < end:
        return start <= value < end
    if start > end:
        return value >= start or value < end
    return False
