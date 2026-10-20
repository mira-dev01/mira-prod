import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import DateRange, date_range_query, get_owned_property, owned_property_ids
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.call_session import CallSession
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.property import Property
from app.models.user import User
from app.schemas.call_quality_event import QualityEventAnalyticsOut
from app.schemas.objection_analytics import ObjectionConversionAnalyticsOut
from app.services import analytics_service, call_service, lead_service
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER
from app.services.lead_temperature import WARM_OR_ABOVE
from app.utils.dates import today_ist

router = APIRouter(prefix="/analytics", tags=["analytics"])

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

    activity = await analytics_service.call_activity(
        db, current_user.id, property_ids, since, until, include_test_calls=include_test_calls
    )
    total_calls = activity["total_calls"]
    completed_calls = activity["completed_calls"]
    qualified_calls = activity["qualified_calls"]
    escalated_calls = activity["escalated_calls"]
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
        Lead.lead_temperature.in_(WARM_OR_ABOVE),
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
            Lead.lead_temperature.in_(WARM_OR_ABOVE),
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
    above -- a busy_recovery_reply notification can have property_id=NULL
    when the guest's property couldn't be resolved (see
    whatsapp_reply_service._resolve_property), which would silently
    undercount under an IN-list filter the same way Notification.property_id
    IS NULL already does for Lead Agent escalations (analytics_summary's own
    comment flags this as a pre-existing gap). Lead.user_id has no such
    nullability gap -- every recovery Lead always belongs to exactly the host
    whose line was busy.
    """
    if date_range.since is not None:
        since = date_range.since
    else:
        since = datetime.now(timezone.utc) - timedelta(days=days)
    until = date_range.until

    metrics = await analytics_service.recovery_metrics(db, current_user.id, since, until)
    busy_calls = metrics["busy_calls"]
    recovered = metrics["recovered"]
    converted = metrics["converted"]
    lost = metrics["lost"]
    avg_recovery_seconds = metrics["avg_recovery_seconds"]
    avg_response_seconds = metrics["avg_response_seconds"]

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


@router.get("/quality-events", response_model=QualityEventAnalyticsOut)
async def analytics_quality_events(
    bucket: str = Query(default="week", pattern="^(week|month)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Cross-call guard/validator-firing analytics (docs/tasks/
    building-intelligence.md, Implementation 3) -- read-only, same shape as
    faq_gaps_analytics (app/api/v1/faq.py), which is this endpoint's direct
    model. Delegates to call_service.quality_event_analytics rather than
    inlining the queries here (unlike analytics_summary/timeseries/recovery
    above) -- this mirrors faq_gaps_analytics's own delegation to
    faq_service.faq_gap_analytics, which is the pattern this task was asked
    to follow, and keeps the aggregation independently unit-testable the
    same way that function is.
    """
    return await call_service.quality_event_analytics(db, current_user.id, bucket)


@router.get("/objection-insights", response_model=ObjectionConversionAnalyticsOut)
async def analytics_objection_insights(
    date_range: DateRange = Depends(date_range_query),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Conversion rate by CallSummary.objection_tags (docs/tasks/
    building-intelligence.md, Implementation 4) -- read-only, informational
    only. Deliberately has NO write path back into NegotiationRule/
    PricingRule/pricing_engine.py -- see lead_service.objection_conversion_
    analytics's own docstring for why. A host reads this and decides
    whether/how to act (e.g. editing their own NegotiationRule policy text),
    same as every other host-authored pricing/negotiation input in this
    codebase; nothing here applies a pricing change automatically.
    """
    return await lead_service.objection_conversion_analytics(db, current_user.id, date_range)


def _reporting_window(date_range: DateRange) -> tuple[date, date]:
    """Inclusive start/end days for the Analytics/Overview endpoints:
    defaults to the last 30 days ending today (IST, same as the dashboard's
    DateRangeProvider), at most a year, start <= end."""
    end = date_range.end_date or today_ist()
    start = date_range.start_date or end - timedelta(days=29)
    if start > end:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "start_date must be on or before end_date")
    if (end - start).days > 365:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Date range can be at most a year")
    return start, end


@router.get("/dashboard")
async def analytics_dashboard(
    property_id: uuid.UUID | None = Query(default=None),
    include_test_calls: bool = Query(default=False),
    compare: bool = Query(default=True),
    date_range: DateRange = Depends(date_range_query),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Everything the host Analytics page shows, in one response: portfolio
    performance (with the previous equal-length period for comparison),
    booking funnel, Mira impact, guest intent, pricing & negotiation, and
    the needs-confirmation counts. Definitions live on each
    analytics_service function. Defaults to the last 30 days (IST) when no
    dates are given, same window as the dashboard's DateRangeProvider."""
    start, end = _reporting_window(date_range)

    if property_id is not None:
        properties = [await get_owned_property(db, property_id, current_user)]
    else:
        properties = list(
            (await db.scalars(select(Property).where(Property.user_id == current_user.id).order_by(Property.name))).all()
        )
    scope = analytics_service.AnalyticsScope(
        host=current_user, properties=properties, start=start, end=end, include_test_calls=include_test_calls
    )
    return await analytics_service.dashboard(db, scope, all_properties=property_id is None, compare=compare)


@router.get("/overview")
async def analytics_overview(
    include_test_calls: bool = Query(default=False),
    date_range: DateRange = Depends(date_range_query),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """The Overview page's snapshot + "Needs your attention" in one request.
    Which numbers follow the selected dates and which are current-state is
    documented on analytics_service.overview. Defaults to the last 30 days
    (IST) when no dates are given."""
    start, end = _reporting_window(date_range)
    properties = list((await db.scalars(select(Property).where(Property.user_id == current_user.id))).all())
    scope = analytics_service.AnalyticsScope(
        host=current_user, properties=properties, start=start, end=end, include_test_calls=include_test_calls
    )
    return await analytics_service.overview(db, scope)
