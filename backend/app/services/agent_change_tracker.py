"""Tracks when a host last changed something the voice agent says or does.

`users.agent_customized_at` is stamped whenever a row in an agent-affecting table is
inserted, updated or deleted. The dashboard compares it with the host's latest browser
test call (see agent_test_status) and highlights "Test your agent" until they test again.

Writing happens in one place -- a SQLAlchemy `before_flush` hook -- so no endpoint can
forget to record a change. What counts is an explicit allowlist below, not "every table":
call logs, leads, bookings and iCal sync change constantly and say nothing about the
agent's behaviour. tests/test_agent_change_tracker.py fails if a new model with an owner
is neither listed here nor in IGNORED_MODELS, so the choice is made on purpose.
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import event, select, update
from sqlalchemy.orm import Session, attributes

from app.models.faq_entry import FaqEntry
from app.models.host_capability import HostCapability
from app.models.negotiation_rule import NegotiationRule
from app.models.policy import Policy, PolicyDocument
from app.models.pricing_rule import PricingRule
from app.models.property import Property
from app.models.user import User

# Owner column per tracked model; PricingRule has no owner of its own (it hangs off a property).
_OWNER_COLUMN: dict[type, str] = {
    FaqEntry: "user_id",
    Policy: "user_id",
    PolicyDocument: "user_id",
    HostCapability: "user_id",
    NegotiationRule: "host_id",
    Property: "user_id",
}

# Property columns the agent reads. Other columns (iCal sync state, photos, counters,
# coordinates, price estimates) change without the agent behaving differently.
_PROPERTY_COLUMNS = frozenset(
    {
        "name", "display_name", "spoken_name", "property_type", "property_style", "brand", "bedroom_count",
        "city", "locality", "base_price", "host_transfer_phone", "usp", "house_rules", "neighborhood_info",
        "landmarks", "faq", "amenities", "check_in_time", "check_out_time", "max_guests", "minimum_nights",
        "saturday_minimum_stay_enabled", "room_rental_enabled", "room_base_price", "rentable_room_count",
        "room_max_guests", "room_details", "seasonal_notes", "agent_config_overrides", "pricing_terms",
        "weekend_rate_enabled", "weekend_price", "call_handling_mode", "call_handling_schedule_start",
        "call_handling_schedule_end", "timezone",
    }
)

# The host's own agent settings, on the users row.
_USER_COLUMNS = frozenset(
    {
        "agent_first_message", "agent_persona", "agent_escalation_phrase", "agent_handoff_phrase",
        "agent_voice_gender", "agent_language_policy", "agent_config",
    }
)

TRACKED_MODELS: tuple[type, ...] = (*_OWNER_COLUMN, PricingRule, User)

# Owned models that deliberately do NOT mark the agent as changed.
IGNORED_MODELS = frozenset(
    {
        "Booking", "CallLease", "CallSession", "GuestProfile", "HoldWaiter", "HostOnboarding", "Lead",
        "Notification", "PaymentRequest", "PriceEvent", "PropertyChunk", "ServiceUsageEvent",
        "SpacezStayRequest", "Technician", "Ticket", "UnansweredQuestion", "UserUiPreference",
    }
)

logger = logging.getLogger(__name__)


def _changed_columns(obj: object, watched: frozenset[str]) -> bool:
    state = attributes.instance_state(obj)
    return any(state.attrs[name].history.has_changes() for name in watched if name in state.attrs)


def _touches_agent(obj: object, *, deleted: bool, new: bool) -> bool:
    if isinstance(obj, Property):
        return deleted or new or _changed_columns(obj, _PROPERTY_COLUMNS)
    if isinstance(obj, User):
        return not new and not deleted and _changed_columns(obj, _USER_COLUMNS)
    return isinstance(obj, tuple(_OWNER_COLUMN) + (PricingRule,))


def _owner_ids(session: Session, objs: list[object]) -> set[uuid.UUID]:
    owners: set[uuid.UUID] = set()
    property_ids: set[uuid.UUID] = set()
    for obj in objs:
        if isinstance(obj, User):
            owners.add(obj.id)
        elif isinstance(obj, PricingRule):
            property_ids.add(obj.property_id)
        else:
            owner = getattr(obj, _OWNER_COLUMN[type(obj)], None)
            if owner is not None:
                owners.add(owner)
    if property_ids:
        owners.update(session.execute(select(Property.user_id).where(Property.id.in_(property_ids))).scalars())
    return owners


def _mark_agent_customized(session: Session, flush_context: object, instances: object) -> None:
    # Bookkeeping must never be able to fail a host's save.
    try:
        _stamp_owners(session)
    except Exception:
        logger.exception("agent change tracking failed")


def _stamp_owners(session: Session) -> None:
    touched: list[object] = []
    touched += [o for o in session.new if _touches_agent(o, deleted=False, new=True)]
    touched += [o for o in session.dirty if session.is_modified(o) and _touches_agent(o, deleted=False, new=False)]
    touched += [o for o in session.deleted if _touches_agent(o, deleted=True, new=False)]
    if not touched:
        return
    owners = _owner_ids(session, touched)
    if owners:
        # Core UPDATE: bypasses the ORM, so it can't re-trigger this hook.
        session.execute(
            update(User).where(User.id.in_(owners)).values(agent_customized_at=datetime.now(timezone.utc))
        )


def install() -> None:
    """Idempotent; called once at import of app.main."""
    if not event.contains(Session, "before_flush", _mark_agent_customized):
        event.listen(Session, "before_flush", _mark_agent_customized)
