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
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER

router = APIRouter(prefix="/analytics", tags=["analytics"])

TimeseriesMetric = Literal["total_calls", "completed_calls", "escalated_calls", "pipeline_value"]


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

    return {
        "window_days": days,
        "start_date": date_range.start_date.isoformat() if date_range.start_date else None,
        "end_date": date_range.end_date.isoformat() if date_range.end_date else None,
        "total_calls": total_calls or 0,
        "completed_calls": completed_calls or 0,
        "escalated_calls": escalated_calls or 0,
        "open_notifications": open_notifications or 0,
        "pipeline_value": float(pipeline_value or 0),
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
    bucket_column = CallSession.created_at if metric != "escalated_calls" else Notification.created_at
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
