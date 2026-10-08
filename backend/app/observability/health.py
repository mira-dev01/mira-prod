"""In-memory, per-process service health registry.

Two kinds of evidence feed each service:
- passive: every real request (record()) -- outcome ok / warn / error, from
  the httpx hook, the voice pipeline, the API middleware, scheduler events.
- probe: a periodic synthetic check (record_probe()) -- ok / degraded / down.

evaluate() folds both into one state per service (up / degraded / down /
unknown / not_configured) with hysteresis, and returns the transitions.
app/services/health_monitor_service.py turns transitions into incidents,
emails and the admin SSE stream.

Call-path contract (same as every optional integration): record() is
synchronous, O(1), touches no I/O and never raises -- it's safe on a live
call's hot path. Nothing here is ever read back into call behavior.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Literal

from app.observability import catalog

logger = logging.getLogger("app.observability.health")

Outcome = Literal["ok", "warn", "error"]
State = Literal["up", "degraded", "down", "unknown", "not_configured"]

WINDOW_SECONDS = 300
PROBE_STALE_SECONDS = 600
PROBE_DOWN_AFTER = 2  # consecutive failed probes
RECOVERY_EVALUATIONS = 2  # consecutive better evaluations before a state improves
_MAX_EVENTS = 1000
_ERROR_LOG_THROTTLE_SECONDS = 10.0
_MAX_ERROR_CHARS = 500

_SEVERITY = {"not_configured": -2, "unknown": -1, "up": 0, "degraded": 1, "down": 2}

# Set while a probe runs so the probe's own HTTP traffic (seen by the httpx
# hook) isn't double-counted as passive traffic.
_suppress_passive: ContextVar[bool] = ContextVar("health_suppress_passive", default=False)


@contextmanager
def suppress_passive() -> Iterator[None]:
    token = _suppress_passive.set(True)
    try:
        yield
    finally:
        _suppress_passive.reset(token)


def passive_suppressed() -> bool:
    return _suppress_passive.get()


def _now() -> float:
    return time.time()


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def _clip(message: str | None) -> str | None:
    if message is None:
        return None
    message = " ".join(str(message).split())
    return message if len(message) <= _MAX_ERROR_CHARS else message[: _MAX_ERROR_CHARS - 1] + "…"


@dataclass
class _Track:
    events: deque = field(default_factory=lambda: deque(maxlen=_MAX_EVENTS))  # (ts, outcome, latency_ms)
    last_error: dict | None = None
    last_ok_at: float | None = None
    last_event_at: float | None = None
    probe_status: str | None = None  # ok / degraded / down
    probe_failures: int = 0
    probe_at: float | None = None
    probe_latency_ms: float | None = None
    probe_error: str | None = None
    detail: dict = field(default_factory=dict)
    not_configured_reason: str | None = None
    state: str = "unknown"
    state_since: float = field(default_factory=_now)
    better_streak: int = 0
    errors_total: int = 0
    last_log_at: float = 0.0
    suppressed_logs: int = 0


_tracks: dict[str, _Track] = {}


def _track(service: str) -> _Track:
    track = _tracks.get(service)
    if track is None:
        track = _tracks[service] = _Track()
    return track


def _call_context() -> dict:
    try:
        from app.services.usage_meter import current_usage_context

        call_session_id, host_id = current_usage_context()
        return {
            "call_session_id": str(call_session_id) if call_session_id else None,
            "host_id": str(host_id) if host_id else None,
        }
    except Exception:
        return {}


def record(
    service: str,
    outcome: Outcome,
    *,
    latency_ms: float | None = None,
    error: str | BaseException | None = None,
    kind: str | None = None,
    op: str | None = None,
) -> None:
    """One real-traffic observation. Never raises."""
    try:
        now = _now()
        track = _track(service)
        track.events.append((now, outcome, latency_ms))
        track.last_event_at = now
        if outcome == "ok":
            track.last_ok_at = now
            return
        if isinstance(error, BaseException):
            kind = kind or type(error).__name__
            error = str(error) or type(error).__name__
        ctx = _call_context()
        track.last_error = {
            "at": _iso(now),
            "message": _clip(error) or kind or outcome,
            "kind": kind,
            "op": op,
            "outcome": outcome,
            **ctx,
        }
        if outcome == "error":
            track.errors_total += 1
        _log_failure(service, track, now, outcome, error, kind, op, latency_ms, ctx)
    except Exception:  # pragma: no cover - must never reach a caller
        pass


def _log_failure(service, track, now, outcome, error, kind, op, latency_ms, ctx) -> None:
    # A dead dependency can fail on every audio chunk; one structured line
    # per service per few seconds (with a suppressed count) is enough.
    if now - track.last_log_at < _ERROR_LOG_THROTTLE_SECONDS:
        track.suppressed_logs += 1
        return
    suppressed, track.suppressed_logs, track.last_log_at = track.suppressed_logs, 0, now
    logger.warning(
        "dependency_%s service=%s op=%s kind=%s error=%s",
        "failure" if outcome == "error" else "warning",
        service,
        op,
        kind,
        _clip(error),
        extra={
            "event": "dependency_failure" if outcome == "error" else "dependency_warning",
            "service": service,
            "op": op,
            "kind": kind,
            "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
            "suppressed_since_last": suppressed,
            **ctx,
        },
    )


def record_probe(
    service: str,
    status: Literal["ok", "degraded", "down"],
    *,
    latency_ms: float | None = None,
    error: str | None = None,
    detail: dict | None = None,
    confirmed: bool = False,
) -> None:
    """One synthetic-check result. A single failed probe is treated as a blip;
    PROBE_DOWN_AFTER consecutive failures mean down -- unless `confirmed`
    (a slow-cadence check like credits, where waiting for a second sample
    would only delay a real signal). Never raises."""
    try:
        track = _track(service)
        track.not_configured_reason = None
        track.probe_at = _now()
        track.probe_latency_ms = latency_ms
        track.probe_status = status
        track.probe_error = _clip(error)
        if detail is not None:
            track.detail = detail
        if status == "down":
            track.errors_total += 1
            track.probe_failures = max(track.probe_failures + 1, PROBE_DOWN_AFTER if confirmed else 0)
            track.last_error = {"at": _iso(track.probe_at), "message": track.probe_error or "probe failed", "kind": "probe", "op": "probe", "outcome": "error"}
            if track.probe_failures == 1:
                logger.warning(
                    "health_probe_failed service=%s error=%s",
                    service,
                    track.probe_error,
                    extra={"event": "health_probe_failed", "service": service},
                )
        else:
            track.probe_failures = 0
            if status == "ok":
                track.last_ok_at = track.probe_at
            elif track.probe_error:
                # Degraded (e.g. Groq serving from a fallback, credits low):
                # the reason has to reach the tile and the alert email.
                track.last_error = {"at": _iso(track.probe_at), "message": track.probe_error, "kind": "probe", "op": "probe", "outcome": "warn"}
    except Exception:  # pragma: no cover
        pass


def record_not_configured(service: str, reason: str, detail: dict | None = None) -> None:
    try:
        track = _track(service)
        track.not_configured_reason = reason
        track.probe_at = _now()
        track.probe_failures = 0
        if detail is not None:
            track.detail = detail
    except Exception:  # pragma: no cover
        pass


def set_detail(service: str, detail: dict) -> None:
    try:
        _track(service).detail = detail
    except Exception:  # pragma: no cover
        pass


def seed_state(service: str, state: str, since: float | None = None) -> None:
    """Restore a state carried over from before a restart (an open incident),
    so recovery still needs fresh evidence and fires a 'resolved' email."""
    track = _track(service)
    track.state = state
    track.state_since = since or _now()


def known_services() -> list[str]:
    configured_static = list(catalog.SERVICES)
    dynamic = [k for k in _tracks if k not in catalog.SERVICES]
    return configured_static + dynamic


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------


def _window_stats(track: _Track, now: float) -> dict:
    cutoff = now - WINDOW_SECONDS
    ok = warn = err = 0
    latencies = []
    for ts, outcome, latency in reversed(track.events):
        if ts < cutoff:
            break
        if outcome == "ok":
            ok += 1
        elif outcome == "warn":
            warn += 1
        else:
            err += 1
        if latency is not None:
            latencies.append(latency)
    total = ok + err
    latencies.sort()
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None
    return {
        "requests": ok + warn + err,
        "ok": ok,
        "warnings": warn,
        "errors": err,
        "error_rate": round(err / total, 3) if total else None,
        "p95_ms": round(p95, 1) if p95 is not None else None,
    }


def _passive_state(stats: dict) -> str:
    ok, err, warn = stats["ok"], stats["errors"], stats["warnings"]
    total = ok + err
    if total == 0 and warn == 0:
        return "unknown"
    if (total >= 4 and err / total >= 0.5) or (err >= 5 and ok == 0):
        return "down"
    if (err >= 2 and total and err / total >= 0.2) or warn >= 3:
        return "degraded"
    if total == 0:
        return "unknown"
    return "up"


def _stale_after(service: str) -> float:
    # Credits are polled every 15 minutes, everything else far more often.
    return 1800.0 if catalog.get(service).group == "credits" else PROBE_STALE_SECONDS


def _configured(service: str, track: _Track) -> bool:
    return track.not_configured_reason is None and catalog.get(service).configured()


def _probe_state(service: str, track: _Track, now: float) -> str:
    if track.probe_at is None or now - track.probe_at > _stale_after(service):
        return "unknown"
    if track.probe_failures >= PROBE_DOWN_AFTER:
        return "down"
    if track.probe_status == "degraded":
        return "degraded"
    return "up"


def _candidate(service: str, track: _Track, now: float) -> tuple[str, dict]:
    stats = _window_stats(track, now)
    if not _configured(service, track):
        return "not_configured", stats
    passive = _passive_state(stats)
    probe = _probe_state(service, track, now)
    worst = max(passive, probe, key=lambda s: _SEVERITY[s])
    if worst == "unknown":
        # No fresh evidence either way: hold the last known state rather than
        # flipping a down service to "unknown" (or green) just because no
        # call has exercised it since.
        worst = track.state if track.state not in ("not_configured",) else "unknown"
    return worst, stats


@dataclass
class Transition:
    service: str
    old: str
    new: str


def evaluate(now: float | None = None) -> list[Transition]:
    """Folds evidence into states. Worsening applies immediately; improving
    needs RECOVERY_EVALUATIONS consecutive better evaluations."""
    now = now or _now()
    transitions: list[Transition] = []
    for service in known_services():
        track = _track(service)
        candidate, _ = _candidate(service, track, now)
        current = track.state
        if candidate == current:
            track.better_streak = 0
            continue
        improving = _SEVERITY[candidate] < _SEVERITY[current] and current in ("degraded", "down")
        if improving and candidate != "not_configured":
            track.better_streak += 1
            if track.better_streak < RECOVERY_EVALUATIONS:
                continue
        track.better_streak = 0
        track.state = candidate
        track.state_since = now
        transitions.append(Transition(service, current, candidate))
        log = logger.warning if _SEVERITY[candidate] > 0 else logger.info
        log(
            "service_state_change service=%s %s->%s",
            service,
            current,
            candidate,
            extra={"event": "service_state_change", "service": service, "old_state": current, "new_state": candidate},
        )
    return transitions


def state_of(service: str) -> str:
    return _track(service).state


def last_error(service: str) -> dict | None:
    return _track(service).last_error


def last_event_at(service: str) -> float | None:
    track = _track(service)
    return max(filter(None, [track.last_event_at, track.probe_at]), default=None)


def errors_total(service: str) -> int:
    return _track(service).errors_total


def recent_errors(service: str) -> int:
    """Failures inside the evaluation window (passive errors + consecutive
    failed probes) -- the evidence that opened an incident."""
    track = _track(service)
    return _window_stats(track, _now())["errors"] + track.probe_failures


def snapshot(now: float | None = None) -> list[dict]:
    now = now or _now()
    out = []
    for service in known_services():
        definition = catalog.get(service)
        track = _track(service)
        stats = _window_stats(track, now)
        latest = max(filter(None, [track.last_event_at, track.probe_at]), default=None)
        out.append(
            {
                "key": service,
                "label": definition.label,
                "group": definition.group,
                "description": definition.description,
                "critical": definition.critical(),
                "state": track.state if _configured(service, track) else "not_configured",
                "not_configured_reason": track.not_configured_reason,
                "since": _iso(track.state_since),
                "last_ok_at": _iso(track.last_ok_at),
                "last_activity_at": _iso(latest),
                # Down/degraded with no evidence for a while = "last known",
                # not necessarily current.
                "stale": latest is None or now - latest > max(WINDOW_SECONDS, _stale_after(service)),
                "last_error": track.last_error,
                "window": stats,
                "probe": {
                    "status": track.probe_status,
                    "at": _iso(track.probe_at),
                    "latency_ms": round(track.probe_latency_ms, 1) if track.probe_latency_ms is not None else None,
                    "consecutive_failures": track.probe_failures,
                    "error": track.probe_error,
                }
                if track.probe_at
                else None,
                "detail": track.detail,
            }
        )
    return out


def reset_for_tests() -> None:
    _tracks.clear()


@contextmanager
def timed(service: str, op: str | None = None) -> Iterator[None]:
    """Synchronous-style wrapper for a non-HTTP dependency call:

        with health.timed("ical_sync", op="fetch"):
            await fetch(...)

    Records ok/error with latency; re-raises the caller's exception unchanged.
    """
    started = time.monotonic()
    try:
        yield
    except Exception as exc:
        record(service, "error", latency_ms=(time.monotonic() - started) * 1000, error=exc, op=op)
        raise
    record(service, "ok", latency_ms=(time.monotonic() - started) * 1000, op=op)
