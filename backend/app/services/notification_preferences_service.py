"""Read/write the host's notification preferences (schemas/
notification_preferences.py) and the small pure helpers every sender uses,
so each consumer applies the same rules:

- get(user): never raises -- a malformed stored document falls back to
  defaults (= pre-settings behaviour) rather than breaking a live call.
- lead_bucket/lead_label: the dashboard's four tiers over the internal
  temperature levels (very_hot shows as Hot; no temperature = Not
  qualified). lead_temperature.py's own levels are untouched.
- host_transfer_phone: the single place that decides which phone a live
  transfer or alert goes to.
"""

import logging

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property import Property
from app.models.user import User
from app.schemas.notification_preferences import NotificationPreferences, NotificationPreferencesUpdate

logger = logging.getLogger(__name__)


def get(user: User) -> NotificationPreferences:
    raw = user.notification_preferences or {}
    try:
        return NotificationPreferences.model_validate(raw)
    except ValidationError:
        logger.warning("Invalid notification_preferences for user %s -- using defaults", user.id, exc_info=True)
        return NotificationPreferences()


async def update(db: AsyncSession, user: User, patch: NotificationPreferencesUpdate) -> NotificationPreferences:
    merged = get(user).model_dump() | patch.model_dump(exclude_unset=True)
    prefs = NotificationPreferences.model_validate(merged)
    # Reassigned (not mutated in place) so the JSONB change is tracked.
    user.notification_preferences = prefs.model_dump(mode="json")
    await db.commit()
    await db.refresh(user)
    return prefs


def lead_bucket(temperature: str | None) -> str:
    if temperature in ("hot", "very_hot"):
        return "hot"
    if temperature in ("warm", "cold"):
        return temperature
    return "not_qualified"


def lead_label(prefs: NotificationPreferences, temperature: str | None) -> str:
    return getattr(prefs.lead_labels, lead_bucket(temperature))


def host_transfer_phone(host: User, property_: Property | None) -> str | None:
    """Where live transfers and host alerts go. "single" mode (the default):
    the account's own number. "per_property": the property's group number,
    falling back to the account number whenever the property has none or
    no property is known yet -- so there is always a destination if the
    host has set any number at all."""
    if get(host).transfer_number_mode == "per_property" and property_ is not None and property_.host_transfer_phone:
        return property_.host_transfer_phone
    return host.phone
