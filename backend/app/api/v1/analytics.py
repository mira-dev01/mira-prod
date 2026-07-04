from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import owned_property_ids
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.call_session import CallSession
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.user import User
from app.services.call_service import BROWSER_TEST_CALLER_NUMBER

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary")
async def analytics_summary(
    days: int = Query(default=30, ge=1, le=365),
    include_test_calls: bool = Query(default=False),
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
    since = datetime.now(timezone.utc) - timedelta(days=days)

    call_filters = [CallSession.user_id == current_user.id, CallSession.created_at >= since]
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
    escalated_calls = await db.scalar(
        select(func.count()).where(
            Notification.property_id.in_(property_ids),
            Notification.channel == "escalation",
            Notification.created_at >= since,
        )
    )
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
    pipeline_value = await db.scalar(
        select(func.coalesce(func.sum(Lead.budget), 0)).where(
            Lead.user_id == current_user.id,
            Lead.lead_temperature.in_(["hot", "warm"]),
            Lead.created_at >= since,
        )
    )
    open_notifications = await db.scalar(
        select(func.count()).where(
            Notification.property_id.in_(property_ids),
            Notification.status == "new",
            Notification.created_at >= since,
        )
    )

    return {
        "window_days": days,
        "total_calls": total_calls or 0,
        "completed_calls": completed_calls or 0,
        "escalated_calls": escalated_calls or 0,
        "open_notifications": open_notifications or 0,
        "pipeline_value": float(pipeline_value or 0),
        "answer_rate": round((completed_calls or 0) / total_calls, 3) if total_calls else None,
    }
