"""Read-only, one-glance view of every host for the internal /admin panel.

admin_monitor_service.hosts() is built from calls, so a host with no calls in
the range simply isn't in it -- the people an operator most needs to notice
(signed up, never connected a number, never got a call) are exactly the ones
it can't show. This module starts from the users table instead and attaches
what each host has set up, what is waiting on them, and what they cost.

Nothing here is read back into a call. All counts come from grouped queries --
one per source, never one per host -- so the roster costs the same for ten
hosts or a thousand.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_session import CallSession
from app.models.host_onboarding import HostOnboarding
from app.models.notification import Notification
from app.models.property import Property
from app.models.ticket import Ticket
from app.models.unanswered_question import UnansweredQuestion
from app.models.user import User
from app.services import admin_monitor_service as monitor
from app.services import agent_config_service, capability_service, ticket_service
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER

# How long a question or an escalation may sit before the operator is told.
OVERDUE_AFTER = timedelta(hours=24)
TAKEOVER_QUEUE_LIMIT = 50
URGENT = ("high", "emergency")


def _owner():
    """An escalation notification has no user column: its host is whoever owns
    the call it came from, or failing that the property it was about."""
    return func.coalesce(CallSession.user_id, Property.user_id)


def _real_calls_only(scope: monitor.Scope) -> list:
    """Same rule as the rest of the admin panel: browser test calls are not a
    host's traffic unless the operator asks to see them."""
    if scope.include_test_calls:
        return []
    return [or_(Notification.call_session_id.is_(None), CallSession.caller_number.is_distinct_from(BROWSER_TEST_CALLER_NUMBER))]


def _only(column, user_id: uuid.UUID | None) -> list:
    return [column == user_id] if user_id is not None else []


async def _call_stats(db: AsyncSession, scope: monitor.Scope, user_id: uuid.UUID | None) -> dict:
    rows = (
        await db.execute(
            select(
                CallSession.user_id,
                func.count(),
                func.sum(case((CallSession.call_type.in_(monitor.MISSED_CALL_TYPES), 1), else_=0)),
                func.max(CallSession.created_at),
            )
            .where(*monitor._call_filters(scope), CallSession.user_id.is_not(None), *_only(CallSession.user_id, user_id))
            .group_by(CallSession.user_id)
        )
    ).all()
    return {uid: {"calls": n, "missed": int(missed or 0), "last_call_at": last} for uid, n, missed, last in rows}


async def _escalation_stats(
    db: AsyncSession, scope: monitor.Scope, now: datetime, user_id: uuid.UUID | None
) -> tuple[dict, dict]:
    """(escalations raised in range, escalations nobody has opened yet)."""
    owner = _owner()

    def base(*conds):
        return (
            select(owner, func.count(), func.min(Notification.created_at))
            .select_from(Notification)
            .outerjoin(CallSession, Notification.call_session_id == CallSession.id)
            .outerjoin(Property, Notification.property_id == Property.id)
            .where(Notification.channel == "escalation", owner.is_not(None), *_real_calls_only(scope), *_only(owner, user_id), *conds)
            .group_by(owner)
        )

    in_range = (
        await db.execute(base(Notification.created_at >= scope.since, Notification.created_at < scope.until))
    ).all()
    unread = (await db.execute(base(Notification.status == "new"))).all()
    cutoff = now - OVERDUE_AFTER
    return (
        {uid: n for uid, n, _ in in_range},
        {
            uid: {"count": n, "oldest_at": oldest, "overdue": oldest is not None and oldest < cutoff}
            for uid, n, oldest in unread
        },
    )


async def _ticket_stats(db: AsyncSession, user_id: uuid.UUID | None) -> dict:
    rows = (
        await db.execute(
            select(
                Ticket.user_id,
                func.count(),
                func.sum(case((Ticket.kind == "missed_call", 1), else_=0)),
                func.sum(case((Ticket.urgency.in_(URGENT), 1), else_=0)),
            )
            .where(Ticket.status == "open", *_only(Ticket.user_id, user_id))
            .group_by(Ticket.user_id)
        )
    ).all()
    return {uid: {"open": n, "takeover": int(t or 0), "urgent": int(u or 0)} for uid, n, t, u in rows}


async def _question_stats(db: AsyncSession, now: datetime, user_id: uuid.UUID | None) -> dict:
    """Distinct pending questions per host (the same question asked five times
    is one thing for the host to answer), and how many have waited too long --
    measured from the first time anyone asked."""
    first_seen = (
        select(
            UnansweredQuestion.user_id.label("user_id"),
            UnansweredQuestion.normalized_question.label("q"),
            func.min(UnansweredQuestion.created_at).label("first_at"),
        )
        .where(UnansweredQuestion.status == "pending", *_only(UnansweredQuestion.user_id, user_id))
        .group_by(UnansweredQuestion.user_id, UnansweredQuestion.normalized_question)
        .subquery()
    )
    cutoff = now - OVERDUE_AFTER
    rows = (
        await db.execute(
            select(
                first_seen.c.user_id,
                func.count(),
                func.min(first_seen.c.first_at),
                func.sum(case((first_seen.c.first_at < cutoff, 1), else_=0)),
            ).group_by(first_seen.c.user_id)
        )
    ).all()
    return {uid: {"pending": n, "oldest_at": oldest, "overdue": int(overdue or 0)} for uid, n, oldest, overdue in rows}


async def _property_stats(db: AsyncSession, user_id: uuid.UUID | None) -> dict:
    has_number = or_(Property.exophone.is_not(None), Property.twilio_number.is_not(None))
    rows = (
        await db.execute(
            select(Property.user_id, func.count(), func.sum(case((has_number, 1), else_=0)))
            .where(*_only(Property.user_id, user_id))
            .group_by(Property.user_id)
        )
    ).all()
    return {uid: {"properties": n, "with_number": int(w or 0)} for uid, n, w in rows}


def _attention(row: dict) -> list[str]:
    """Why an operator should look at this host, most pressing first. Empty
    means nothing is waiting on anyone."""
    reasons: list[str] = []
    if row["takeover"]:
        reasons.append(f"{row['takeover']} missed call{'s' if row['takeover'] != 1 else ''} to take over")
    if row["escalations_overdue"]:
        reasons.append("an escalation unopened for over a day")
    if row["questions_overdue"]:
        reasons.append(
            f"{row['questions_overdue']} question{'s' if row['questions_overdue'] != 1 else ''} unanswered over a day"
        )
    if row["urgent_tickets"]:
        reasons.append(f"{row['urgent_tickets']} urgent ticket{'s' if row['urgent_tickets'] != 1 else ''}")
    if row["properties"] == 0:
        reasons.append("no properties yet")
    elif not row["number_connected"]:
        reasons.append("no phone number connected")
    if row["onboarding"] == "in_progress":
        reasons.append("setup unfinished")
    return reasons


async def _host_rows(db: AsyncSession, scope: monitor.Scope, user_id: uuid.UUID | None = None) -> list[dict]:
    now = datetime.now(timezone.utc)
    users = (
        await db.execute(
            select(User).where(*_only(User.id, user_id)).order_by(User.created_at.desc())
        )
    ).scalars().all()
    calls = await _call_stats(db, scope, user_id)
    escalations, unread = await _escalation_stats(db, scope, now, user_id)
    tickets = await _ticket_stats(db, user_id)
    questions = await _question_stats(db, now, user_id)
    properties = await _property_stats(db, user_id)
    onboarding = dict(
        (await db.execute(select(HostOnboarding.user_id, HostOnboarding.status).where(*_only(HostOnboarding.user_id, user_id)))).all()
    )
    cost = await monitor._cost_by_user(db, scope)

    rows = []
    for user in users:
        c = calls.get(user.id, {})
        t = tickets.get(user.id, {})
        q = questions.get(user.id, {})
        p = properties.get(user.id, {})
        e = unread.get(user.id, {})
        config = agent_config_service.get_config(user)
        row = {
            "user_id": user.id,
            "name": user.business_name or user.name or user.email,
            "email": user.email,
            "joined_at": user.created_at,
            "status": user.status,
            "onboarding": onboarding.get(user.id),
            "properties": p.get("properties", 0),
            # A host can be reached through any property's number or their
            # portfolio ("lead") line.
            "number_connected": bool(p.get("with_number") or user.lead_exophone or user.twilio_lead_number),
            "busy_mode": config.busy_calls.mode,
            "calls": c.get("calls", 0),
            "missed_calls": c.get("missed", 0),
            "last_call_at": c.get("last_call_at"),
            "escalations": escalations.get(user.id, 0),
            "escalations_unread": e.get("count", 0),
            "escalations_overdue": bool(e.get("overdue")),
            "open_tickets": t.get("open", 0),
            "urgent_tickets": t.get("urgent", 0),
            "takeover": t.get("takeover", 0),
            "questions_pending": q.get("pending", 0),
            "questions_overdue": q.get("overdue", 0),
            "oldest_question_at": q.get("oldest_at"),
            "cost_inr": round(cost.get(user.id, 0.0), 2),
        }
        row["attention"] = _attention(row)
        rows.append(row)
    return rows


async def _takeover_queue(db: AsyncSession) -> list[dict]:
    """Open "call this guest back" tickets across every host, longest-waiting
    first -- the calls Mira could not take, and nobody has picked up."""
    rows = (
        await db.execute(
            select(Ticket, User.business_name, User.name, User.email, Property.name)
            .join(User, User.id == Ticket.user_id)
            .outerjoin(Property, Property.id == Ticket.property_id)
            .where(Ticket.status == "open", Ticket.kind == "missed_call")
            .order_by(Ticket.created_at.asc())
            .limit(TAKEOVER_QUEUE_LIMIT)
        )
    ).all()
    return [
        {
            "id": ticket.id,
            "user_id": ticket.user_id,
            "host": business or name or email,
            "property_name": property_name,
            "summary": ticket.summary,
            "detail": ticket.detail,
            "urgency": ticket.urgency,
            "created_at": ticket.created_at,
        }
        for ticket, business, name, email, property_name in rows
    ]


async def roster(db: AsyncSession, scope: monitor.Scope) -> dict:
    """Every host, those needing attention first, plus the takeover queue."""
    hosts = await _host_rows(db, scope)
    hosts.sort(key=lambda h: (-len(h["attention"]), -h["calls"], h["name"] or ""))
    return {"hosts": hosts, "takeover": await _takeover_queue(db)}


async def detail(db: AsyncSession, user_id: uuid.UUID, scope: monitor.Scope) -> dict | None:
    user = await db.get(User, user_id)
    if user is None:
        return None
    (summary,) = await _host_rows(db, scope, user_id)

    property_rows = (
        await db.execute(select(Property).where(Property.user_id == user_id).order_by(Property.name))
    ).scalars().all()
    calls_by_property = dict(
        (
            await db.execute(
                select(CallSession.property_id, func.count())
                .where(*monitor._call_filters(scope), CallSession.user_id == user_id, CallSession.property_id.is_not(None))
                .group_by(CallSession.property_id)
            )
        ).all()
    )
    first_seen = (
        select(
            UnansweredQuestion.property_id.label("property_id"),
            UnansweredQuestion.normalized_question.label("q"),
        )
        .where(UnansweredQuestion.user_id == user_id, UnansweredQuestion.status == "pending")
        .group_by(UnansweredQuestion.property_id, UnansweredQuestion.normalized_question)
        .subquery()
    )
    pending_by_property = dict(
        (await db.execute(select(first_seen.c.property_id, func.count()).group_by(first_seen.c.property_id))).all()
    )
    properties = [
        {
            "id": p.id,
            "name": p.display_name or p.name,
            "city": p.city,
            "number_connected": bool(p.exophone or p.twilio_number),
            "payment_override": bool(p.agent_config_overrides),
            "calls": calls_by_property.get(p.id, 0),
            "questions_pending": pending_by_property.get(p.id, 0),
        }
        for p in property_rows
    ]

    recent_calls = [
        {
            "id": c.id,
            "created_at": c.created_at,
            "caller_number": c.caller_number,
            "call_type": c.call_type,
            "status": c.status,
            "urgency": c.urgency,
            "duration_s": (c.ended_at - c.started_at).total_seconds() if c.started_at and c.ended_at else None,
        }
        for c in (
            await db.execute(
                select(CallSession)
                .where(*monitor._call_filters(scope), CallSession.user_id == user_id)
                .order_by(CallSession.created_at.desc())
                .limit(15)
            )
        ).scalars()
    ]

    question_rows = (
        await db.execute(
            select(
                func.min(UnansweredQuestion.question),
                func.count(),
                func.min(UnansweredQuestion.created_at),
                Property.name,
            )
            .outerjoin(Property, Property.id == UnansweredQuestion.property_id)
            .where(UnansweredQuestion.user_id == user_id, UnansweredQuestion.status == "pending")
            .group_by(UnansweredQuestion.normalized_question, UnansweredQuestion.property_id, Property.name)
            .order_by(func.min(UnansweredQuestion.created_at).asc())
            .limit(25)
        )
    ).all()
    cutoff = datetime.now(timezone.utc) - OVERDUE_AFTER
    questions = [
        {"question": q, "asked": n, "first_asked_at": first, "property_name": prop, "overdue": first < cutoff}
        for q, n, first, prop in question_rows
    ]

    owner = _owner()
    escalations = [
        {"message": n.message, "urgency": n.urgency, "status": n.status, "created_at": n.created_at}
        for n in (
            await db.execute(
                select(Notification)
                .outerjoin(CallSession, Notification.call_session_id == CallSession.id)
                .outerjoin(Property, Notification.property_id == Property.id)
                .where(Notification.channel == "escalation", owner == user_id, *_real_calls_only(scope))
                .order_by(Notification.created_at.desc())
                .limit(10)
            )
        ).scalars()
    ]

    try:
        capabilities = [
            {"id": c.id, "name": c.name, "enabled": c.enabled, "state": c.state}
            for c in (await capability_service.host_capabilities(db, user)).capabilities
        ]
    except Exception:  # the roster must still render if one capability check breaks
        capabilities = []

    config = agent_config_service.get_config(user)
    return {
        "summary": summary,
        "profile": {
            "phone": user.phone,
            "tier": user.tier,
            "timezone": user.timezone,
            "lead_number": user.lead_exophone or user.twilio_lead_number,
            "notification_email": user.notification_email,
        },
        "agent": config.model_dump(mode="json"),
        "capabilities": capabilities,
        "properties": properties,
        "recent_calls": recent_calls,
        "tickets": await ticket_service.list_tickets(db, user_id, status="open", limit=25),
        "questions": questions,
        "escalations": escalations,
    }
