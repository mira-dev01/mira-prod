from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import owned_property_ids
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.call_session import CallSession
from app.models.notification import Notification
from app.models.user import User

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary")
async def analytics_summary(
    days: int = Query(default=30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # CallSession metrics scoped by user_id, not property ownership -- Lead
    # Agent calls have no single property (property_id is NULL) but still
    # belong to a host.
    property_ids = await owned_property_ids(db, current_user)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    base = select(CallSession).where(CallSession.user_id == current_user.id, CallSession.created_at >= since)

    total_calls = await db.scalar(select(func.count()).select_from(base.subquery()))
    completed_calls = await db.scalar(
        select(func.count()).select_from(base.where(CallSession.status == "completed").subquery())
    )
    escalated_calls = await db.scalar(
        select(func.count()).select_from(base.where(CallSession.urgency.isnot(None)).subquery())
    )
    revenue_attributed = await db.scalar(
        select(func.coalesce(func.sum(CallSession.revenue_attributed), 0)).where(
            CallSession.user_id == current_user.id, CallSession.created_at >= since
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
        "revenue_attributed": float(revenue_attributed or 0),
        "answer_rate": round((completed_calls or 0) / total_calls, 3) if total_calls else None,
    }
