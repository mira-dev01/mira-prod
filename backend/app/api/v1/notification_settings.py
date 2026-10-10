from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.schemas.notification_preferences import (
    DEFAULT_CALL_SUMMARY_BODY,
    DEFAULT_CALL_SUMMARY_SUBJECT,
    TEMPLATE_PLACEHOLDERS,
    EmailPreviewIn,
    EmailPreviewOut,
    NotificationPreferencesUpdate,
    NotificationSettingsOut,
)
from app.services import notification_preferences_service
from app.services.call_summary_email import CallSummaryFacts, render_call_summary_email

# Settings > Your account > Notifications & escalations. Always the signed-in
# host's own preferences. The recipient email itself stays on PATCH
# /auth/me (User.notification_email), as before.
router = APIRouter(prefix="/notification-settings", tags=["notification-settings"])


def _out(user: User) -> NotificationSettingsOut:
    return NotificationSettingsOut(
        preferences=notification_preferences_service.get(user),
        placeholders=list(TEMPLATE_PLACEHOLDERS),
        default_subject=DEFAULT_CALL_SUMMARY_SUBJECT,
        default_body=DEFAULT_CALL_SUMMARY_BODY,
    )


@router.get("", response_model=NotificationSettingsOut)
async def get_notification_settings(current_user: User = Depends(get_current_user)) -> NotificationSettingsOut:
    return _out(current_user)


@router.patch("", response_model=NotificationSettingsOut)
async def update_notification_settings(
    payload: NotificationPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotificationSettingsOut:
    await notification_preferences_service.update(db, current_user, payload)
    return _out(current_user)


@router.post("/preview", response_model=EmailPreviewOut)
async def preview_call_summary_email(
    payload: EmailPreviewIn, current_user: User = Depends(get_current_user)
) -> EmailPreviewOut:
    """Renders a draft subject/body with sample call data through the same
    renderer the real email uses."""
    prefs = notification_preferences_service.get(current_user)
    sample = CallSummaryFacts(
        property_name="Sea View Villa",
        guest_name="Priya Sharma",
        guest_phone="+91 98765 43210",
        call_type="BOOKING_LEAD",
        conversation_summary=(
            "Priya asked about a 3-night stay from 12 Dec for 4 guests, confirmed availability and asked "
            "for the best price for the full stay."
        ),
        duration_minutes=4.5,
        started_at=datetime.now(timezone.utc),
        lead_temperature="hot",
        call_page_url=f"{settings.frontend_base_url}/dashboard/calls",
    )
    subject, _, html = render_call_summary_email(
        # The preview shows exactly the draft: an empty subject/body means
        # the default, not whatever is currently saved.
        prefs,
        sample,
        subject_template=payload.subject or DEFAULT_CALL_SUMMARY_SUBJECT,
        body_template=payload.body or "",
    )
    return EmailPreviewOut(subject=subject, html=html)
