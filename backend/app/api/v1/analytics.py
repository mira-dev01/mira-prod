from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import DateRange, date_range_query, owned_property_ids
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.call_session import CallSession
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.user import User
from app.schemas.call_classification import QUALIFIED_CALL_TYPES
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER
from app.services.recovery_service import NOTIFICATION_CHANNEL_BUSY_RECOVERY

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Historical-only as of the WhatsApp production-messaging cutover: the
# interactive numbered-menu guest reply (Property/Pricing/FAQs/Photos/
# Talk-to-host/Something else) that used to produce this channel has been
# removed in favor of a single "Mira is busy, call back in 5 minutes"
# message (see recovery_service._guest_recovery_whatsapp_text) -- nothing
# creates a busy_recovery_reply Notification anymore. Kept as a plain
# string (not re-imported from a service module) purely so `recovered`/
# `avg_recovery_time_seconds` below still aggregate correctly over any
# pre-cutover rows already in the database; both will read 0/null for any
# lead created after the cutover, which is expected, not a bug.
NOTIFICATION_CHANNEL_BUSY_RECOVERY_REPLY = "busy_recovery_reply"

TimeseriesMetric = Literal["total_calls", "completed_calls", "escalated_calls", "pipeline_value", "open_leads"]


@router.get("/summary")
async def analytics_summary(
    days: int = Query(default=30, ge=1, le=365),
    include_test_calls: bool = Query(default=False),
    date_range: DateRange = Depends(date_range_query),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # CallSession metrics scoped by user_id, not property ownership -- Lead
    # Agent calls have no single property (property_id is NULL) but still
    # belong to a host.
    #
    # Browser-test calls are excluded by default -- they're internal QA, not
    # real guest calls, and counting them would distort answer_rate/revenue
    # with whatever the host happens to be testing that day. They still show
    # up normally on the Calls list (labeled "Browser test") regardless.
    # include_test_calls=true (the Overview page's toggle) lifts that filter
    # for hosts who are still in a testing phase and want to see their own
    # QA activity reflected here.
    property_ids = await owned_property_ids(db, current_user)

    # date_range (explicit start_date/end_date) takes precedence; `days`
    # remains the fallback so any caller that doesn't send explicit dates
    # keeps today's behavior unchanged.
    if date_range.since is not None:
        since = date_range.since
    else:
        since = datetime.now(timezone.utc) - timedelta(days=days)
    until = date_range.until

    call_filters = [CallSession.user_id == current_user.id, CallSession.created_at >= since]
    if until is not None:
        call_filters.append(CallSession.created_at < until)
    if not include_test_calls:
        call_filters.append(CallSession.caller_number != BROWSER_TEST_CALLER_NUMBER)

    base = select(CallSession).where(*call_filters)

    total_calls = await db.scalar(select(func.count()).select_from(base.subquery()))
    completed_calls = await db.scalar(
        select(func.count()).select_from(base.where(CallSession.status == "completed").subquery())
    )
    # "Qualified" (BOOKING_LEAD/GUEST_SUPPORT/EXISTING_BOOKING/GENERAL_QUERY)
    # is never itself a stored call_type value -- see schemas/
    # call_classification.py -- just this derived grouping, computed here
    # the same way escalated_calls is derived from Notification.channel
    # rather than a stored boolean.
    qualified_calls = await db.scalar(
        select(func.count()).select_from(base.where(CallSession.call_type.in_(QUALIFIED_CALL_TYPES)).subquery())
    )
    # CallSession.urgency is never written anywhere in the app (escalations
    # are recorded as Notification rows, not on the CallSession itself) --
    # counting it here always returned 0, contradicting the Live Requests
    # panel on the same Overview page, which is populated from Notification
    # rows with channel="escalation". Count that instead, so this card
    # matches what the host actually sees in Live Requests.
    #
    # NOTE: like Live Requests itself, this is scoped by
    # property_id IN owned_property_ids, so a Lead Agent escalation
    # (property_id=NULL, portfolio-wide calls) won't be counted here either
    # -- pre-existing gap in Live Requests' own query, not introduced by this
    # fix. Tracked as a follow-up, not fixed here to keep this change scoped.
    escalated_filters = [
        Notification.property_id.in_(property_ids),
        Notification.channel == "escalation",
        Notification.created_at >= since,
    ]
    if until is not None:
        escalated_filters.append(Notification.created_at < until)
    escalated_calls = await db.scalar(select(func.count()).where(*escalated_filters))
    # CallSession.revenue_attributed has no writer anywhere in the app (no
    # booking-confirmation hook sets it) -- it would always read as 0,
    # despite Live Requests showing calls with real guest-stated prices.
    # There's no booking-with-a-price entity in the schema yet either
    # (Booking is iCal-synced calendar data with no price field). The
    # closest honest, structured signal is Lead.budget -- what the guest
    # told Mira they're willing to pay, captured for hot/warm leads during
    # qualification. This is pipeline potential, not confirmed revenue, so
    # the stat card label is changed accordingly (see AnalyticsSummary.
    # revenue_attributed -> pipeline_value on the frontend).
    pipeline_filters = [
        Lead.user_id == current_user.id,
        Lead.lead_temperature.in_(["hot", "warm"]),
        Lead.created_at >= since,
    ]
    if until is not None:
        pipeline_filters.append(Lead.created_at < until)
    pipeline_value = await db.scalar(select(func.coalesce(func.sum(Lead.budget), 0)).where(*pipeline_filters))
    open_notification_filters = [
        Notification.property_id.in_(property_ids),
        Notification.status == "new",
        Notification.created_at >= since,
    ]
    if until is not None:
        open_notification_filters.append(Notification.created_at < until)
    open_notifications = await db.scalar(select(func.count()).where(*open_notification_filters))

    # "Open Leads" overview card: leads the host hasn't marked contacted/
    # booked/closed yet. Distinct from lead_temperature (hot/warm/cold),
    # which is qualification, not follow-up status.
    open_leads_filters = [Lead.user_id == current_user.id, Lead.status == "open", Lead.created_at >= since]
    if until is not None:
        open_leads_filters.append(Lead.created_at < until)
    open_leads = await db.scalar(select(func.count()).where(*open_leads_filters))

    return {
        "window_days": days,
        "start_date": date_range.start_date.isoformat() if date_range.start_date else None,
        "end_date": date_range.end_date.isoformat() if date_range.end_date else None,
        "total_calls": total_calls or 0,
        "completed_calls": completed_calls or 0,
        "qualified_calls": qualified_calls or 0,
        "escalated_calls": escalated_calls or 0,
        "open_notifications": open_notifications or 0,
        "pipeline_value": float(pipeline_value or 0),
        "open_leads": open_leads or 0,
        "answer_rate": round((completed_calls or 0) / total_calls, 3) if total_calls else None,
    }


@router.get("/timeseries")
async def analytics_timeseries(
    metric: TimeseriesMetric = Query(...),
    include_test_calls: bool = Query(default=False),
    date_range: DateRange = Depends(date_range_query),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Self-sufficient default window -- if both dates are omitted, behave
    # like "last 30 days" rather than requiring the caller to always specify.
    if date_range.start_date is not None and date_range.end_date is not None:
        start_date = date_range.start_date
        end_date = date_range.end_date
    else:
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=29)

    since = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    until = datetime.combine(end_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)

    # date_trunc() buckets in the connection's session timezone, not UTC --
    # explicitly convert to UTC first so bucket dates line up with the
    # UTC-day semantics used by DateRange/since/until everywhere else in this
    # endpoint (otherwise a non-UTC server timezone shifts records into the
    # wrong day, silently dropping the last day's data from the response).
    #
    # bucket_column must come from whichever table each metric actually
    # groups by -- day's underlying column reference forces that table into
    # the query's FROM clause, so picking the wrong one creates an unrelated
    # implicit cross join with whatever table the WHERE filters are actually
    # scoping (confirmed bug: pipeline_value's filters are all on Lead, but
    # bucket_column was always CallSession.created_at, silently cross-joining
    # call_sessions × leads and inflating every day's summed budget).
    if metric == "escalated_calls":
        bucket_column = Notification.created_at
    elif metric in ("pipeline_value", "open_leads"):
        bucket_column = Lead.created_at
    else:
        bucket_column = CallSession.created_at
    day = func.date_trunc("day", func.timezone("UTC", bucket_column))

    if metric == "total_calls":
        call_filters = [
            CallSession.user_id == current_user.id,
            CallSession.created_at >= since,
            CallSession.created_at < until,
        ]
        if not include_test_calls:
            call_filters.append(CallSession.caller_number != BROWSER_TEST_CALLER_NUMBER)
        rows = (
            await db.execute(
                select(day.label("bucket"), func.count().label("value")).where(*call_filters).group_by(day)
            )
        ).all()
    elif metric == "completed_calls":
        call_filters = [
            CallSession.user_id == current_user.id,
            CallSession.status == "completed",
            CallSession.created_at >= since,
            CallSession.created_at < until,
        ]
        if not include_test_calls:
            call_filters.append(CallSession.caller_number != BROWSER_TEST_CALLER_NUMBER)
        rows = (
            await db.execute(
                select(day.label("bucket"), func.count().label("value")).where(*call_filters).group_by(day)
            )
        ).all()
    elif metric == "pipeline_value":
        lead_filters = [
            Lead.user_id == current_user.id,
            Lead.lead_temperature.in_(["hot", "warm"]),
            Lead.created_at >= since,
            Lead.created_at < until,
        ]
        rows = (
            await db.execute(
                select(day.label("bucket"), func.coalesce(func.sum(Lead.budget), 0).label("value"))
                .where(*lead_filters)
                .group_by(day)
            )
        ).all()
    elif metric == "open_leads":
        open_leads_filters = [
            Lead.user_id == current_user.id,
            Lead.status == "open",
            Lead.created_at >= since,
            Lead.created_at < until,
        ]
        rows = (
            await db.execute(
                select(day.label("bucket"), func.count().label("value")).where(*open_leads_filters).group_by(day)
            )
        ).all()
    else:  # escalated_calls
        property_ids = await owned_property_ids(db, current_user)
        notif_filters = [
            Notification.property_id.in_(property_ids),
            Notification.channel == "escalation",
            Notification.created_at >= since,
            Notification.created_at < until,
        ]
        rows = (
            await db.execute(
                select(day.label("bucket"), func.count().label("value")).where(*notif_filters).group_by(day)
            )
        ).all()

    by_date = {row.bucket.date().isoformat(): row.value for row in rows}

    # DB group-by only returns days with data -- zero-fill every day in
    # [start_date, end_date] in Python; trivial at ~30-180 points.
    points = []
    cursor = start_date
    while cursor <= end_date:
        iso = cursor.isoformat()
        value = by_date.get(iso, 0)
        points.append({"date": iso, "value": float(value) if metric == "pipeline_value" else int(value)})
        cursor += timedelta(days=1)

    return {"metric": metric, "points": points}


@router.get("/recovery")
async def analytics_recovery(
    days: int = Query(default=30, ge=1, le=365),
    date_range: DateRange = Depends(date_range_query),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Busy Call Recovery funnel/KPIs (documentation/architecture: Phase 7).
    Read-only aggregate queries over rows Phase 3/6 already write during a
    live call/WhatsApp reply -- nothing here writes anything, subscribes to
    anything, or sits anywhere near the voice pipeline or webhook request
    path, so this can never add latency or behavior change to a real call or
    WhatsApp reply. Same query style as analytics_summary/analytics_timeseries
    above (inline ORM aggregates, no service layer -- this file has never had
    one, see its own module-level precedent).

    Every recovery row is scoped by Lead.user_id (a join through Lead), not
    property_id.in_(owned_property_ids) like escalated_calls/open_notifications
    above -- a busy_recovery notification can have property_id=NULL for a
    Lead Agent/portfolio-wide line with no single property, which would
    silently undercount under an IN-list filter the same way
    Notification.property_id IS NULL already does for Lead Agent
    escalations (analytics_summary's own comment flags this as a
    pre-existing gap). Lead.user_id has no such
    nullability gap -- every recovery Lead always belongs to exactly the host
    whose line was busy.
    """
    if date_range.since is not None:
        since = date_range.since
    else:
        since = datetime.now(timezone.utc) - timedelta(days=days)
    until = date_range.until

    window_filters = [Notification.created_at >= since]
    if until is not None:
        window_filters.append(Notification.created_at < until)

    # Busy Calls: one row per rejected call attempt (recovery_service.py
    # creates a fresh Notification every time, unlike Lead, which reuses an
    # existing open/contacted lead across repeat attempts from the same
    # guest -- see recovery_service.py's own docstring). Counting Lead rows
    # instead would undercount repeat-caller volume.
    busy_calls = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .join(Lead, Notification.lead_id == Lead.id)
        .where(
            Notification.channel == NOTIFICATION_CHANNEL_BUSY_RECOVERY,
            Lead.user_id == current_user.id,
            *window_filters,
        )
    )

    # Recovered: the guest engaged back on WhatsApp at least once. Counted
    # per distinct Lead (not per reply row) -- a guest who replies twice to
    # the same busy-rejection thread is one recovered guest, not two.
    recovered = await db.scalar(
        select(func.count(func.distinct(Notification.lead_id)))
        .select_from(Notification)
        .join(Lead, Notification.lead_id == Lead.id)
        .where(
            Notification.channel == NOTIFICATION_CHANNEL_BUSY_RECOVERY_REPLY,
            Lead.user_id == current_user.id,
            *window_filters,
        )
    )

    # Converted / Lost: Lead.status is the one and only sales-pipeline
    # signal in this codebase (host-set via PATCH /leads/{id}, see
    # docs/api.md's leads.py entry) -- "booked"/"closed" on a recovery lead
    # (recovery_reason IS NOT NULL) is exactly what these mean, same
    # convention analytics_summary's open_leads/pipeline_value already use
    # for the general Lead funnel, filtered additionally to recovery leads.
    # Windowed by Lead.created_at (when the recovery lead was born), not
    # Notification.created_at, since a lead can convert well after the
    # window that produced the original busy_recovery notification.
    lead_window_filters = [Lead.created_at >= since]
    if until is not None:
        lead_window_filters.append(Lead.created_at < until)
    converted = await db.scalar(
        select(func.count()).where(
            Lead.user_id == current_user.id,
            Lead.recovery_reason.is_not(None),
            Lead.status == "booked",
            *lead_window_filters,
        )
    )
    lost = await db.scalar(
        select(func.count()).where(
            Lead.user_id == current_user.id,
            Lead.recovery_reason.is_not(None),
            Lead.status == "closed",
            *lead_window_filters,
        )
    )

    # Average Recovery Time: time from the busy-rejection notification to
    # this guest's FIRST reply, per lead, then averaged across leads.
    # func.min() on each side collapses repeat busy-rejections/repeat
    # replies for the same lead down to first-attempt -> first-reply, which
    # is the "how long until the guest re-engaged" question this KPI asks --
    # not every possible attempt/reply pairing.
    busy_first = (
        select(Notification.lead_id, func.min(Notification.created_at).label("busy_at"))
        .where(Notification.channel == NOTIFICATION_CHANNEL_BUSY_RECOVERY, Notification.lead_id.is_not(None))
        .group_by(Notification.lead_id)
        .subquery()
    )
    reply_first = (
        select(Notification.lead_id, func.min(Notification.created_at).label("reply_at"))
        .where(Notification.channel == NOTIFICATION_CHANNEL_BUSY_RECOVERY_REPLY, Notification.lead_id.is_not(None))
        .group_by(Notification.lead_id)
        .subquery()
    )
    avg_recovery_seconds = await db.scalar(
        select(func.avg(func.extract("epoch", reply_first.c.reply_at - busy_first.c.busy_at)))
        .select_from(busy_first)
        .join(reply_first, reply_first.c.lead_id == busy_first.c.lead_id)
        .join(Lead, Lead.id == busy_first.c.lead_id)
        .where(Lead.user_id == current_user.id, busy_first.c.busy_at >= since)
    )

    # Average Host Response: time from the busy_recovery notification being
    # created to the host first marking it read (Notification.responded_at,
    # set once by notification_service.mark_read -- see that field's own
    # comment on why updated_at isn't reused for this).
    avg_response_seconds = await db.scalar(
        select(func.avg(func.extract("epoch", Notification.responded_at - Notification.created_at)))
        .select_from(Notification)
        .join(Lead, Notification.lead_id == Lead.id)
        .where(
            Notification.channel == NOTIFICATION_CHANNEL_BUSY_RECOVERY,
            Notification.responded_at.is_not(None),
            Lead.user_id == current_user.id,
            *window_filters,
        )
    )

    busy_calls = busy_calls or 0
    recovered = recovered or 0
    converted = converted or 0
    lost = lost or 0

    return {
        "window_days": days,
        "start_date": date_range.start_date.isoformat() if date_range.start_date else None,
        "end_date": date_range.end_date.isoformat() if date_range.end_date else None,
        "busy_calls": busy_calls,
        "recovered": recovered,
        "converted": converted,
        "lost": lost,
        # float(), not bare round() -- func.avg(func.extract(...)) comes
        # back as a Decimal, same as pipeline_value's func.sum() above; a
        # bare round(Decimal, 1) stays a Decimal, which this app's JSON
        # encoding renders as a string rather than a number.
        "avg_recovery_time_seconds": round(float(avg_recovery_seconds), 1) if avg_recovery_seconds is not None else None,
        "avg_host_response_seconds": round(float(avg_response_seconds), 1) if avg_response_seconds is not None else None,
        "recovery_rate": round(recovered / busy_calls, 3) if busy_calls else None,
        "conversion_rate": round(converted / busy_calls, 3) if busy_calls else None,
        "funnel": [
            {"stage": "busy_calls", "label": "Busy Calls", "value": busy_calls},
            {"stage": "recovered", "label": "Recovered", "value": recovered},
            {"stage": "converted", "label": "Converted", "value": converted},
        ],
    }
