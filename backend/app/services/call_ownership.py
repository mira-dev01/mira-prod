"""resolve_effective_call_owner: the one pure domain decision behind Call
Ownership routing -- "given this property, its host, and the current UTC
time, who owns an inbound guest call right now: HOST or MIRA."

Deliberately its own module rather than folded into app/services/
call_service.py (which owns DID -> Property/User resolution, a related but
distinct concern -- DB-backed CRUD/lookup helpers, not domain math) or
app/utils/ (this has real branching logic and its own exception type,
unlike the trivial one-line helpers living there today, e.g.
app/utils/phone.py).

Purity is the whole point of this module: no DB session, no Redis, no HTTP,
no logging, no mutation, no global/module-level state. Same
(property, host, current_time_utc) in, same CallOwner out, every time --
this is what lets the Exotel call-routing webhook and guest_calling_
notification call it inline, synchronously, on every inbound call with zero
I/O cost.

Routing input as of documentation/host-call-hours-and-handoff.md: the
account-global window on User (host_call_hours_enabled/_start/_end/
_timezone). The per-property Property.call_handling_mode/schedule/timezone
columns are NO LONGER read here -- they remain in the schema, staged for
removal (same as the CallLease Postgres table). Precedence:
  1. settings.fixed_host_hours_* env override (TEMPORARY, see below) --
     forces one hardcoded IST window for every property/host.
  2. host.host_call_hours_enabled -- the real, editable path.
  3. neither -> MIRA (Mira answers 24/7), today's default behavior.
"""

import enum
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings
from app.models.property import Property
from app.models.user import User

# TEMPORARY fixed-hours override, see Settings.fixed_host_hours_start/_end's
# own comment in config.py: until the account-global host-call-hours path
# (host.host_call_hours_*) has been verified in production, every property
# is forced onto one hardcoded Asia/Kolkata HOST window instead of the
# host's own configured window. Both settings unset (the default) = this
# block never applies, zero behavior change from before this override
# existed. Delete this constant and the override branch in
# resolve_effective_call_owner below (and the render.yaml keys) once the
# account-global path is trusted -- do not build anything further on top of
# it, it exists to be removed.
_FIXED_HOST_HOURS_TIMEZONE = "Asia/Kolkata"


class CallOwner(enum.Enum):
    """The resolver's only two possible answers -- a concrete HOST or MIRA
    for one instant in time. "The host has a call-hours window configured"
    is a *configuration*, never an *answer*: the whole point of this
    function is collapsing that window down to HOST or MIRA for the current
    moment.

    A real enum.Enum, not a plain string constant pair, so a typo like
    CallOwner.MRIA fails at attribute-access time rather than producing a
    silently-wrong string a future routing branch would never match
    against "MIRA"/"HOST" -- same reasoning, and the same plain
    enum.Enum(no str mixin) shape, as this codebase's one other enum,
    app/services/call_coordinator.py's Decision ("the only two outcomes
    acquire_or_reject() can hand back... nothing else for a caller to
    branch on"). Every model column still deliberately uses a plain string
    instead -- this enum is scoped tightly to this function's in-memory
    return value only, never persisted, so it doesn't collide with or
    duplicate that convention.
    """

    HOST = "HOST"
    MIRA = "MIRA"


class InvalidCallOwnershipConfigError(ValueError):
    """Raised when the stored call-ownership configuration cannot be
    resolved -- host_call_hours_enabled set with a missing bound, an
    unparseable HH:MM string, or a timezone identifier ZoneInfo doesn't
    recognize. Mirrors NegotiationPolicyParseError's precedent (app/
    services/negotiation_policy_service.py): a plain, locally-scoped
    exception, and the same "callers must not silently fall back" contract
    -- this resolver never guesses a default HOST/MIRA answer or a default
    timezone on bad input, it raises. In practice this should be
    unreachable in production: app/schemas/user.py's UserUpdate validators
    already reject an invalid time-format/timezone/incomplete-window
    combination before a row can be saved via the API. This exists as a
    defensive contract for this function's own callers, not because bad
    rows are expected to exist -- e.g. a row written directly against the
    DB, bypassing the API validators, or a caller that constructs a User
    object in memory without going through UserUpdate at all.
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


def resolve_effective_call_owner(
    property_: Property, host: User, current_time_utc: datetime
) -> CallOwner:
    """Who owns an inbound call for `property_` (owned by `host`) at
    `current_time_utc`.

    `property_` is still accepted (callers already have it, and a future
    per-property override could reintroduce it) but is not currently read
    -- the routing input is the account-global window on `host`.

    `current_time_utc` must be a timezone-aware datetime (any aware
    timezone is accepted and converted internally -- it does not need to
    already be UTC specifically, since .astimezone() handles the
    conversion -- but callers should pass real UTC: the webhook resolves
    "now" once, in UTC, at call arrival). A naive datetime raises
    InvalidCallOwnershipConfigError rather than silently being treated as
    UTC -- guessing a naive datetime's timezone is exactly the kind of
    silent assumption this function's whole design refuses to make.

    Precedence (see the module docstring):
      1. settings.fixed_host_hours_* env override (TEMPORARY) -- one
         hardcoded IST window for everyone.
      2. host.host_call_hours_enabled -- evaluate the host's own window.
      3. neither -> MIRA (24/7).
    A window (either source) needs both bounds and a valid timezone; see
    InvalidCallOwnershipConfigError's docstring for why a missing/invalid
    one raises instead of guessing.
    """
    if current_time_utc.tzinfo is None:
        raise InvalidCallOwnershipConfigError(
            "current_time_utc must be timezone-aware; a naive datetime cannot be safely assumed to be UTC"
        )

    if settings.fixed_host_hours_start and settings.fixed_host_hours_end:
        # TEMPORARY: bypasses the host's own window entirely -- see
        # _FIXED_HOST_HOURS_TIMEZONE's comment above.
        return _owner_for_window(
            current_time_utc,
            start_raw=settings.fixed_host_hours_start,
            end_raw=settings.fixed_host_hours_end,
            tz_name=_FIXED_HOST_HOURS_TIMEZONE,
            field_prefix="fixed_host_hours",
        )

    if not host.host_call_hours_enabled:
        # No window configured -- Mira answers 24/7. The per-property
        # Property.call_handling_* columns are deliberately NOT consulted
        # here (staged for removal).
        return CallOwner.MIRA

    return _owner_for_window(
        current_time_utc,
        start_raw=host.host_call_hours_start,
        end_raw=host.host_call_hours_end,
        tz_name=host.host_call_hours_timezone,
        field_prefix="host_call_hours",
    )


def _owner_for_window(
    current_time_utc: datetime,
    *,
    start_raw: str | None,
    end_raw: str | None,
    tz_name: str | None,
    field_prefix: str,
) -> CallOwner:
    """Shared tail for both the env-override and the per-host window: given
    a start/end/timezone, is `current_time_utc` inside the HOST window?
    Kept as one function so both entry points apply identical parsing and
    identical failure semantics rather than two near-copies drifting."""
    if start_raw is None or end_raw is None:
        raise InvalidCallOwnershipConfigError(
            f"{field_prefix}: both start and end are required when a call-hours window is active"
        )

    if not tz_name:
        raise InvalidCallOwnershipConfigError(f"{field_prefix}: a timezone is required when a call-hours window is active")

    try:
        zone = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise InvalidCallOwnershipConfigError(
            f"{field_prefix}: timezone={tz_name!r} is not a valid IANA timezone identifier"
        ) from exc

    local_time = current_time_utc.astimezone(zone).time()
    start = _parse_hh_mm(start_raw, field_name=f"{field_prefix}_start")
    end = _parse_hh_mm(end_raw, field_name=f"{field_prefix}_end")

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
