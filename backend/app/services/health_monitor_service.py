"""Turns live service health (app/observability/health.py) into incidents,
emails and the /admin home page's real-time feed.

Scheduled from app/main.py:
- tick() every TICK_SECONDS: evaluate states -> open/update/resolve
  ServiceIncident rows -> send due alert emails -> wake SSE listeners.
- probe_cycle() every 60s: free synthetic checks (app/observability/probes.py).
- credits_cycle() every 15 min: the admin Balances data (prepaid remaining,
  live provider balances, quotas) as "credits:<account>" services, so a
  running-out account alerts the same way an outage does.
- send_daily_digest() at settings.health_digest_hour_utc:minute_utc.

Alert policy (only where settings.health_alerts_active -- production by
default; dev and prod watch the same accounts, so one sender is enough):
- down -> immediate email ("URGENT" when the service is on the call path).
- degraded for >= health_degraded_alert_minutes -> one warning email.
- still down after health_alert_reminder_minutes AND still failing -> reminder.
- resolved -> recovery email (only if anything was sent for that incident).
One incident = at most one email of each kind, deduped in the DB, so a
restart/redeploy mid-incident doesn't re-alert.

Never on a call path, never raises out of a scheduled job.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from app.config import settings
from app.database import AsyncSessionLocal
from app.integrations import email_client, email_templates
from app.models.service_incident import ServiceIncident
from app.observability import catalog, health, probes

logger = logging.getLogger(__name__)

TICK_SECONDS = 10
_SEND_RETRY_SECONDS = 120
_INCIDENT_HISTORY_HOURS = 24

_loaded = False
_open: dict[str, uuid.UUID] = {}  # service -> open incident id
_count_offset: dict[str, int] = {}  # service -> error_count already stored when we started tracking it
_count_baseline: dict[str, int] = {}  # service -> health.errors_total() at that moment
_incidents_cache: list[dict] = []
_memory_alerts: dict[str, dict] = {}  # service -> {opened_at, first_error}, only while the DB is unreachable
_last_send_failure_at: float = 0.0
_probe_cycle = 0
_update_event: asyncio.Event | None = None
_version = 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _env() -> str:
    return settings.environment


def admin_url() -> str:
    return f"{settings.frontend_base_url.rstrip('/')}/admin"


# --------------------------------------------------------------------------
# real-time listeners (SSE)
# --------------------------------------------------------------------------


def _event() -> asyncio.Event:
    global _update_event
    if _update_event is None:
        _update_event = asyncio.Event()
    return _update_event


def _notify() -> None:
    global _update_event, _version
    _version += 1
    previous = _event()
    _update_event = asyncio.Event()
    previous.set()


async def wait_for_update(timeout: float) -> None:
    try:
        await asyncio.wait_for(_event().wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass


# --------------------------------------------------------------------------
# incidents
# --------------------------------------------------------------------------


def _incident_dict(incident: ServiceIncident, now: datetime) -> dict:
    """Cached form. duration_s / down_seconds of an OPEN incident keep
    growing after the cache was built -- _live() recomputes them."""
    end = incident.resolved_at or now
    detail = incident.detail or {}
    return {
        "_down_since": detail.get("down_since"),
        "_down_closed": float(detail.get("down_seconds") or 0),
        "id": str(incident.id),
        "service": incident.service,
        "label": incident.label,
        "group": catalog.get(incident.service).group,
        "severity": incident.severity,
        "state": incident.current_state,
        "opened_at": incident.opened_at.isoformat(),
        "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
        "duration_s": round((end - incident.opened_at).total_seconds()),
        "down_seconds": _down_seconds(detail, now),
        "first_error": incident.first_error,
        "last_error": incident.last_error,
        "error_count": incident.error_count,
        "sample_call_session_id": incident.sample_call_session_id,
        "alerts_sent": incident.alerts_sent,
        "last_alert_at": incident.last_alert_at.isoformat() if incident.last_alert_at else None,
    }


def _live(incident: dict, now: datetime) -> dict:
    out = {k: v for k, v in incident.items() if not k.startswith("_")}
    if incident["resolved_at"] is None:
        out["duration_s"] = round((now - datetime.fromisoformat(incident["opened_at"])).total_seconds())
        down = incident["_down_closed"]
        if incident["_down_since"]:
            down += (now - datetime.fromisoformat(incident["_down_since"])).total_seconds()
        out["down_seconds"] = round(down)
    return out


def _down_seconds(detail: dict, now: datetime) -> float:
    total = float(detail.get("down_seconds") or 0)
    if detail.get("down_since"):
        total += (now - datetime.fromisoformat(detail["down_since"])).total_seconds()
    return round(total)


def _track_down_time(incident: ServiceIncident, new_state: str, now: datetime) -> None:
    detail = dict(incident.detail or {})
    if new_state == "down" and not detail.get("down_since"):
        detail["down_since"] = now.isoformat()
    elif new_state != "down" and detail.get("down_since"):
        detail["down_seconds"] = _down_seconds(detail, now)
        detail.pop("down_since")
    incident.detail = detail


async def _refresh_cache(db) -> None:
    global _incidents_cache
    now = _utcnow()
    rows = (
        await db.scalars(
            select(ServiceIncident)
            .where(
                ServiceIncident.environment == _env(),
                or_(
                    ServiceIncident.resolved_at.is_(None),
                    ServiceIncident.resolved_at >= now - timedelta(hours=_INCIDENT_HISTORY_HOURS),
                ),
            )
            .order_by(ServiceIncident.opened_at.desc())
            .limit(200)
        )
    ).all()
    _incidents_cache = [_incident_dict(r, now) for r in rows]


async def _ensure_loaded() -> bool:
    """Picks up incidents left open by the previous process so a redeploy
    mid-outage continues the same incident (no duplicate alert) and still
    sends the 'resolved' email once the service recovers."""
    global _loaded
    if _loaded:
        return True
    try:
        async with AsyncSessionLocal() as db:
            open_rows = (
                await db.scalars(
                    select(ServiceIncident).where(
                        ServiceIncident.environment == _env(), ServiceIncident.resolved_at.is_(None)
                    )
                )
            ).all()
            for row in open_rows:
                _open[row.service] = row.id
                _count_offset[row.service] = row.error_count
                _count_baseline[row.service] = health.errors_total(row.service)
                health.seed_state(row.service, row.current_state, row.opened_at.timestamp())
                if row.service.startswith(catalog.CREDITS_PREFIX):
                    catalog.set_label(row.service, row.label)
            await _refresh_cache(db)
        _loaded = True
        return True
    except Exception:
        logger.exception("health_monitor_load_failed -- will retry next tick")
        return False


def _error_text(service: str) -> str | None:
    last = health.last_error(service)
    return last.get("message") if last else None


def _call_id(service: str) -> str | None:
    last = health.last_error(service)
    return last.get("call_session_id") if last else None


async def _apply_transitions(db, transitions: list[health.Transition], now: datetime) -> bool:
    changed = False
    for t in transitions:
        incident_id = _open.get(t.service)
        incident = await db.get(ServiceIncident, incident_id) if incident_id else None
        if t.new in ("degraded", "down"):
            if incident is None:
                incident = ServiceIncident(
                    environment=_env(),
                    service=t.service,
                    label=catalog.get(t.service).label,
                    severity=t.new,
                    current_state=t.new,
                    opened_at=now,
                    first_error=_error_text(t.service),
                    last_error=_error_text(t.service),
                    error_count=0,
                    sample_call_session_id=_call_id(t.service),
                    detail={},
                )
                db.add(incident)
                await db.flush()
                _open[t.service] = incident.id
                _count_offset[t.service] = 0
                _count_baseline[t.service] = max(0, health.errors_total(t.service) - health.recent_errors(t.service))
            else:
                incident.current_state = t.new
                if t.new == "down":
                    incident.severity = "down"
            _track_down_time(incident, t.new, now)
            changed = True
        elif t.new in ("up", "not_configured") and incident is not None:
            incident.current_state = t.new
            incident.resolved_at = now
            _track_down_time(incident, t.new, now)
            _open.pop(t.service, None)
            changed = True
            if incident.alerts_sent and t.new == "up":
                await _send_alert("resolved", incident, now)
    return changed


def _sync_counts(incident: ServiceIncident) -> bool:
    service = incident.service
    count = _count_offset.get(service, 0) + max(0, health.errors_total(service) - _count_baseline.get(service, 0))
    error = _error_text(service)
    call_id = _call_id(service)
    changed = False
    if count != incident.error_count:
        incident.error_count = count
        changed = True
    if error and error != incident.last_error:
        incident.last_error = error
        incident.first_error = incident.first_error or error
        changed = True
    if call_id and not incident.sample_call_session_id:
        incident.sample_call_session_id = call_id
        changed = True
    return changed


async def _maybe_alert(incident: ServiceIncident, now: datetime) -> bool:
    if not settings.health_alerts_active:
        return False
    state = incident.current_state
    if state == "down" and not incident.down_alert_sent:
        return await _send_alert("down", incident, now)
    if (
        state == "degraded"
        and not incident.degraded_alert_sent
        and not incident.down_alert_sent
        and now - incident.opened_at >= timedelta(minutes=settings.health_degraded_alert_minutes)
    ):
        return await _send_alert("degraded", incident, now)
    if (
        state == "down"
        and not incident.service.startswith(catalog.CREDITS_PREFIX)
        and incident.last_alert_at is not None
        and now - incident.last_alert_at >= timedelta(minutes=settings.health_alert_reminder_minutes)
        and incident.error_count > incident.errors_at_last_alert
    ):
        return await _send_alert("reminder", incident, now)
    return False


async def _reconcile_missing(db, now: datetime) -> bool:
    """A service can be failing with no open incident if its transition
    happened while the incident store (Postgres) was unreachable -- open one
    now, carrying over any alert already sent from memory so it isn't
    re-sent."""
    changed = False
    for service in health.known_services():
        state = health.state_of(service)
        if state not in ("degraded", "down") or service in _open:
            continue
        memory = _memory_alerts.pop(service, None)
        incident = ServiceIncident(
            environment=_env(),
            service=service,
            label=catalog.get(service).label,
            severity=state,
            current_state=state,
            opened_at=memory["opened_at"] if memory else now,
            first_error=(memory or {}).get("first_error") or _error_text(service),
            last_error=_error_text(service),
            error_count=0,
            sample_call_session_id=_call_id(service),
            down_alert_sent=bool(memory),
            alerts_sent=1 if memory else 0,
            last_alert_at=memory["opened_at"] if memory else None,
            detail={"down_since": now.isoformat()} if state == "down" else {},
        )
        db.add(incident)
        await db.flush()
        _open[service] = incident.id
        _count_offset[service] = 0
        _count_baseline[service] = max(0, health.errors_total(service) - health.recent_errors(service))
        changed = True
    return changed


def _transient_incident(service: str, now: datetime, opened_at: datetime, first_error: str | None) -> ServiceIncident:
    return ServiceIncident(
        environment=_env(),
        service=service,
        label=catalog.get(service).label,
        severity="down",
        current_state="down",
        opened_at=opened_at,
        first_error=first_error,
        last_error=_error_text(service),
        error_count=health.recent_errors(service),
        sample_call_session_id=_call_id(service),
        down_alert_sent=False,
        degraded_alert_sent=False,
        alerts_sent=0,
        errors_at_last_alert=0,
        detail={},
    )


async def _alert_from_memory(transitions: list[health.Transition], now: datetime, *, recoveries_only: bool = False) -> None:
    """Incident store unreachable (usually: Postgres itself is down) -- the
    one outage that must still email. Down/recovered only, deduped in
    memory; _reconcile_missing records it properly once the DB is back."""
    if not settings.health_alerts_active:
        return
    for t in transitions:
        if not recoveries_only and t.new == "down" and t.service not in _open and t.service not in _memory_alerts:
            first_error = _error_text(t.service)
            if await _send_alert("down", _transient_incident(t.service, now, now, first_error), now):
                _memory_alerts[t.service] = {"opened_at": now, "first_error": first_error}
        elif t.new == "up" and t.service in _memory_alerts:
            memory = _memory_alerts.pop(t.service)
            incident = _transient_incident(t.service, now, memory["opened_at"], memory["first_error"])
            incident.current_state = "up"
            await _send_alert("resolved", incident, now)


async def tick() -> None:
    """Never raises."""
    try:
        loaded = await _ensure_loaded()
        transitions = health.evaluate()
        now = _utcnow()
        if not loaded:
            await _alert_from_memory(transitions, now)
            return
        # Something alerted from memory recovered right as the DB came back.
        await _alert_from_memory(transitions, now, recoveries_only=True)
        try:
            async with AsyncSessionLocal() as db:
                changed = await _apply_transitions(db, transitions, now)
                if await _reconcile_missing(db, now):
                    changed = True
                for service, incident_id in list(_open.items()):
                    incident = await db.get(ServiceIncident, incident_id)
                    if incident is None:
                        _open.pop(service, None)
                        continue
                    if _sync_counts(incident):
                        changed = True
                    if await _maybe_alert(incident, now):
                        changed = True
                if changed:
                    await db.commit()
                    await _refresh_cache(db)
        except Exception:
            logger.exception("health_monitor_store_failed -- alerting from memory until the DB is reachable")
            await _alert_from_memory(transitions, now)
    except Exception:
        logger.exception("health_monitor_tick_failed")
    finally:
        _notify()


# --------------------------------------------------------------------------
# email
# --------------------------------------------------------------------------


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes}m"
    return f"{hours // 24}d {hours % 24}h"


def _fmt_time(value: datetime) -> str:
    ist = value.astimezone(timezone(timedelta(hours=5, minutes=30)))
    return ist.strftime("%d %b %Y, %I:%M %p IST")


async def _deliver(subject: str, text: str, html: str) -> bool:
    """True if at least one recipient got it. Resend being down is itself
    tracked (the httpx hook records the failed send) and shown on the page."""
    global _last_send_failure_at
    if time.monotonic() - _last_send_failure_at < _SEND_RETRY_SECONDS and _last_send_failure_at:
        return False
    delivered = False
    for recipient in settings.alert_email_list:
        try:
            result = await email_client.send_email(recipient, subject, text, html_body=html)
            if result.get("status") == "sent":
                delivered = True
            else:
                logger.warning("health_alert_email_skipped to=%s reason=%s", recipient, result.get("reason"))
        except Exception:
            logger.exception("health_alert_email_failed to=%s subject=%s", recipient, subject)
    if not delivered:
        _last_send_failure_at = time.monotonic()
    return delivered


async def _send_alert(kind: str, incident: ServiceIncident, now: datetime) -> bool:
    definition = catalog.get(incident.service)
    critical = definition.critical()
    env_tag = f"[{settings.environment}]"
    duration = _fmt_duration((now - incident.opened_at).total_seconds())
    is_credits = definition.group == "credits"
    if is_credits:
        subjects = {
            "down": f"🔴 URGENT · Mira {env_tag} · {incident.label} credits critically low -- recharge now",
            "degraded": f"🟠 Mira {env_tag} · {incident.label} credits running low",
            "reminder": f"🔴 Mira {env_tag} · {incident.label} credits still critically low",
            "resolved": f"🟢 Mira {env_tag} · {incident.label} credits topped up",
        }
    else:
        subjects = {
            "down": f"{'🔴 URGENT' if critical else '🔴'} Mira {env_tag} · {incident.label} is DOWN",
            "degraded": f"🟠 Mira {env_tag} · {incident.label} degraded for {duration}",
            "reminder": f"🔴 Still down ({duration}) · Mira {env_tag} · {incident.label}",
            "resolved": f"🟢 Resolved · Mira {env_tag} · {incident.label} recovered after {duration}",
        }
    state = "up" if kind == "resolved" else incident.current_state
    html = email_templates.build_health_alert_email_html(
        kind=kind,
        label=incident.label,
        state=state,
        description=definition.description,
        critical=critical,
        is_credits=is_credits,
        opened_at=_fmt_time(incident.opened_at),
        duration=duration if kind != "down" else None,
        first_error=incident.first_error,
        last_error=incident.last_error,
        error_count=incident.error_count,
        sample_call_session_id=incident.sample_call_session_id,
        admin_url=admin_url(),
    )
    text = "\n".join(
        line
        for line in [
            subjects[kind],
            f"Service: {incident.label} ({definition.description})",
            f"Started: {_fmt_time(incident.opened_at)}",
            f"Duration: {duration}" if kind != "down" else "",
            f"Failures seen: {incident.error_count}" if incident.error_count else "",
            f"First error: {incident.first_error}" if incident.first_error else "",
            f"Latest error: {incident.last_error}" if incident.last_error and incident.last_error != incident.first_error else "",
            f"Example call: {incident.sample_call_session_id}" if incident.sample_call_session_id else "",
            f"Admin panel: {admin_url()}",
        ]
        if line
    )
    if not await _deliver(subjects[kind], text, html):
        return False
    if kind == "down":
        incident.down_alert_sent = True
    elif kind == "degraded":
        incident.degraded_alert_sent = True
    incident.alerts_sent += 1
    incident.last_alert_at = now
    incident.errors_at_last_alert = incident.error_count
    logger.info(
        "health_alert_sent kind=%s service=%s",
        kind,
        incident.service,
        extra={"event": "health_alert_sent", "service": incident.service, "alert_kind": kind},
    )
    return True


# --------------------------------------------------------------------------
# probes + credits
# --------------------------------------------------------------------------


async def probe_cycle() -> None:
    global _probe_cycle
    try:
        await probes.run_probes(_probe_cycle)
    except Exception:
        logger.exception("health_probe_cycle_failed")
    _probe_cycle += 1


def _credit_summary(card: dict) -> str:
    currency = card.get("currency") or ""
    symbol = "₹" if currency == "INR" else "$" if currency == "USD" else ""
    balance = card.get("balance")
    parts = []
    if balance is not None:
        parts.append(f"{symbol}{balance:,.2f} {card.get('unit') or ''}".strip() if symbol else f"{balance:,.0f} {card.get('unit') or ''}".strip())
    elif card.get("used") is not None and card.get("limit"):
        parts.append(f"{symbol}{card['used']:,.2f} used of {symbol}{card['limit']:,.2f}")
    if card.get("remaining_share") is not None:
        parts.append(f"{card['remaining_share'] * 100:.0f}% left")
    if card.get("days_left") is not None:
        parts.append(f"~{card['days_left']} days at current burn")
    return ", ".join(parts)


async def credits_cycle() -> list[dict]:
    """Never raises. Returns the balance cards it used (digest reuses them)."""
    from app.services import admin_monitor_service

    try:
        async with AsyncSessionLocal() as db:
            cards = await admin_monitor_service.balances(db)
    except Exception:
        logger.exception("health_credits_cycle_failed")
        return []
    for card in cards:
        key = catalog.credits_key(card["account"])
        catalog.set_label(key, card.get("label") or card["account"])
        summary = _credit_summary(card)
        detail = {
            k: card.get(k)
            for k in ("kind", "currency", "balance", "used", "limit", "unit", "remaining_share", "days_left", "burn_per_day", "level")
        }
        detail["summary"] = summary
        status = card.get("status")
        if status in ("not_set", "not_configured"):
            health.record_not_configured(key, card.get("detail") or status, detail=detail)
            continue
        if status == "error":
            # The provider's balance API failed -- we don't know the balance,
            # which isn't the same as running out. Keep the last known state.
            detail["error"] = card.get("detail")
            health.set_detail(key, detail)
            continue
        level = card.get("level")
        balance = card.get("balance")
        if (balance is not None and balance <= 0) or level == "critical":
            health.record_probe(key, "down", error=f"Critically low: {summary}", detail=detail, confirmed=True)
        elif level == "low":
            health.record_probe(key, "degraded", error=f"Running low: {summary}", detail=detail)
        else:
            health.record_probe(key, "ok", detail=detail)
    return cards


# --------------------------------------------------------------------------
# snapshot (admin API / SSE)
# --------------------------------------------------------------------------


def _availability(service: str, incidents: list[dict], now: datetime) -> float | None:
    window_start = now - timedelta(hours=24)
    down = 0.0
    for incident in incidents:
        if incident["service"] != service:
            continue
        opened = datetime.fromisoformat(incident["opened_at"])
        resolved = datetime.fromisoformat(incident["resolved_at"]) if incident["resolved_at"] else now
        if resolved <= window_start:
            continue
        overlap = (resolved - max(opened, window_start)).total_seconds()
        span = max((resolved - opened).total_seconds(), 1)
        # Only the down part of an incident counts against availability,
        # pro-rated to the slice of the incident inside the 24h window.
        down += incident["down_seconds"] * min(1.0, overlap / span)
    return round(max(0.0, 1 - down / 86400), 4)


def _overall(services: list[dict]) -> str:
    states = {s["state"] for s in services if s["group"] != "credits"}
    if "down" in states:
        return "down"
    if "degraded" in states:
        return "degraded"
    return "up"


def snapshot() -> dict:
    now = _utcnow()
    incidents = [_live(i, now) for i in _incidents_cache]
    services = health.snapshot()
    for service in services:
        service["open_incident_id"] = str(_open[service["key"]]) if service["key"] in _open else None
        service["availability_24h"] = _availability(service["key"], incidents, now)
        service["incidents_24h"] = sum(1 for i in incidents if i["service"] == service["key"])
    counts: dict[str, int] = {}
    for service in services:
        counts[service["state"]] = counts.get(service["state"], 0) + 1
    groups = []
    for group in catalog.GROUP_ORDER:
        members = [s for s in services if s["group"] == group]
        if members:
            groups.append({"key": group, "label": catalog.GROUP_LABELS[group], "services": members})
    return {
        "environment": settings.environment,
        "generated_at": now.isoformat(),
        "version": _version,
        "alerts_enabled": settings.health_alerts_active,
        "alert_recipients": settings.alert_email_list,
        "overall": _overall(services),
        "counts": counts,
        "groups": groups,
        "incidents": incidents,
    }


# --------------------------------------------------------------------------
# daily digest
# --------------------------------------------------------------------------


async def send_daily_digest(*, force: bool = False) -> dict:
    """force=True sends even where alerts are off (the admin 'send now'
    button). Never raises."""
    global _last_send_failure_at
    if not (force or settings.health_alerts_active):
        return {"status": "skipped", "reason": "alerts disabled in this environment"}
    try:
        await _ensure_loaded()
        cards = await credits_cycle()
        snap = snapshot()
        now = _utcnow()
        services = [
            {
                "label": s["label"],
                "group_label": catalog.GROUP_LABELS[s["group"]],
                "state": s["state"],
                "availability": f"{s['availability_24h'] * 100:.2f}%" if s["availability_24h"] is not None else "—",
                "incidents_24h": s["incidents_24h"],
                "note": (s["last_error"] or {}).get("message") if s["state"] in ("down", "degraded") else (s.get("not_configured_reason") or ""),
            }
            for g in snap["groups"]
            if g["key"] != "credits"
            for s in g["services"]
        ]
        incidents = [
            {
                "label": i["label"],
                "severity": i["severity"],
                "opened_at": _fmt_time(datetime.fromisoformat(i["opened_at"])),
                "duration": _fmt_duration(i["duration_s"]),
                "resolved": i["resolved_at"] is not None,
                "first_error": i["first_error"],
            }
            for i in snap["incidents"]
        ]
        credits = [
            {
                "label": c.get("label") or c["account"],
                "level": c.get("level") if c.get("status") == "ok" else None,
                "remaining": _credit_summary(c) or c.get("detail") or c.get("status"),
                "days_left": c.get("days_left"),
            }
            for c in cards
        ]
        overall = snap["overall"]
        down = [s["label"] for s in services if s["state"] == "down"]
        degraded = [s["label"] for s in services if s["state"] == "degraded"]
        headline = (
            f"{len(down)} down" if down else f"{len(degraded)} degraded" if degraded else "All systems operational"
        )
        low_credits = [c["label"] for c in credits if c["level"] in ("low", "critical")]
        if low_credits:
            headline += f" · low credits: {', '.join(low_credits)}"
        date_label = _fmt_time(now).split(",")[0]
        subject = f"Mira daily health [{settings.environment}] · {date_label} · {headline}"
        html = email_templates.build_health_digest_email_html(
            overall=overall,
            services=services,
            incidents=incidents,
            credits=credits,
            admin_url=admin_url(),
            date_label=date_label,
        )
        text_lines = [subject, ""]
        text_lines += [f"Credits · {c['label']}: {c['remaining']}" for c in credits]
        text_lines += [""] + [f"Incident · {i['label']} ({i['severity']}) {i['opened_at']} · {i['duration']}" for i in incidents]
        text_lines += [""] + [f"{s['label']}: {s['state']} · {s['availability']}" for s in services]
        text_lines += ["", f"Admin panel: {admin_url()}"]
        _last_send_failure_at = 0.0  # a digest is a deliberate send; don't let an earlier failure gate it
        delivered = await _deliver(subject, "\n".join(text_lines), html)
        return {"status": "sent" if delivered else "failed", "recipients": settings.alert_email_list}
    except Exception:
        logger.exception("health_digest_failed")
        return {"status": "failed"}


def reset_for_tests() -> None:
    global _loaded, _incidents_cache, _last_send_failure_at, _probe_cycle, _update_event
    _loaded = False
    _open.clear()
    _count_offset.clear()
    _count_baseline.clear()
    _memory_alerts.clear()
    _incidents_cache = []
    _last_send_failure_at = 0.0
    _probe_cycle = 0
    _update_event = None
