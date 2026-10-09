"""Per-host capability state: activation, dependency validation and
readiness, on top of the registry (app/services/capability_registry.py).

Readiness is always derived from real data (properties, numbers,
technicians, FAQs...) on read -- never stored -- so it can't drift from what
the voice agent actually has to work with.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.faq_entry import FaqEntry
from app.models.host_capability import HostCapability
from app.models.negotiation_rule import NegotiationRule
from app.models.property import Property
from app.models.technician import Technician
from app.models.user import User
from app.schemas.capability import (
    CapabilityCatalogOut,
    CapabilityDefinitionOut,
    CapabilityGroupOut,
    CapabilityStatusOut,
    CapabilityUpdate,
    HostCapabilitiesOut,
    IntegrationStatusOut,
    RequirementDefOut,
    RequirementOut,
)
from app.services.capability_registry import (
    CAPABILITIES,
    GROUPS,
    Capability,
    dependents_of,
    get_capability,
)

logger = logging.getLogger(__name__)


class CapabilityNotFoundError(Exception):
    pass


class CapabilityChangeBlockedError(Exception):
    """The requested change would contradict live backend behavior or break
    another enabled capability -- surfaced as a 409 with this message."""


class CapabilityConfigError(ValueError):
    pass


@dataclass
class HostSnapshot:
    user: User
    property_count: int = 0
    has_property_phone: bool = False
    has_priceable_property: bool = False
    has_ical_link: bool = False
    has_live_pricing_property: bool = False
    has_property_photos: bool = False
    has_legacy_property_faq: bool = False
    technician_count: int = 0
    verified_faq_count: int = 0
    approved_rule_count: int = 0
    rows: dict[str, HostCapability] = field(default_factory=dict)


def _has_usable_phone(phone: str | None) -> bool:
    # Same >= 10-digit rule request_host_transfer uses
    # (tool_handlers._has_usable_host_phone) to decide whether a live
    # transfer can be attempted at all.
    return bool(phone) and sum(ch.isdigit() for ch in phone) >= 10


_REQUIREMENT_CHECKS: dict[str, Callable[[HostSnapshot], bool]] = {
    "has_property": lambda s: s.property_count > 0,
    "property_phone": lambda s: s.has_property_phone,
    "lead_number": lambda s: bool(s.user.lead_exophone or s.user.twilio_lead_number),
    "host_phone": lambda s: _has_usable_phone(s.user.phone),
    # Mirrors recommend_properties' own "priceable" filter
    # (property/retrieval/filter_builder.py) and the ₹0 quote guards.
    "priceable_property": lambda s: s.has_priceable_property,
    "negotiation_policy": lambda s: s.approved_rule_count > 0 or s.user.max_discount_percent_override is not None,
    "live_pricing_property": lambda s: s.has_live_pricing_property,
    "ical_linked": lambda s: s.has_ical_link,
    "technician_on_file": lambda s: s.technician_count > 0,
    "property_photos": lambda s: s.has_property_photos,
    "faq_knowledge": lambda s: s.verified_faq_count > 0 or s.has_legacy_property_faq,
}


def _twilio_whatsapp_connected() -> bool:
    return bool(
        settings.twilio_enabled
        and settings.twilio_account_sid
        and settings.twilio_auth_token
        and (settings.twilio_messaging_service_sid or settings.twilio_whatsapp_from)
    )


_INTEGRATIONS: dict[str, tuple[str, Callable[[], bool]]] = {
    "twilio_whatsapp": ("WhatsApp (Twilio)", _twilio_whatsapp_connected),
    "redis": ("Call coordination (Redis)", lambda: bool(settings.redis_url)),
    "email": ("Email (Resend)", lambda: bool(settings.resend_api_key)),
    "searchapi": ("Airbnb live prices (SearchApi)", lambda: bool(settings.searchapi_api_key)),
    "bright_data": ("Airbnb listing import (Bright Data)", lambda: bool(settings.bright_data_api_key)),
}


def catalog() -> CapabilityCatalogOut:
    return CapabilityCatalogOut(
        groups=[CapabilityGroupOut(id=g.id, name=g.name, description=g.description) for g in GROUPS],
        capabilities=[
            CapabilityDefinitionOut(
                id=c.id,
                name=c.name,
                description=c.description,
                benefit=c.benefit,
                group=c.group,
                activation=c.activation,
                enforcement=c.enforcement,
                default_enabled=c.default_enabled,
                selectable=c.selectable,
                hard_dependencies=list(c.hard_dependencies),
                soft_dependencies=list(c.soft_dependencies),
                requirements=[
                    RequirementDefOut(
                        id=r.id, label=r.label, hard=r.hard, action_label=r.action_label, action_route=r.action_route
                    )
                    for r in c.requirements
                ],
                integrations=[i.id for i in c.integrations],
                routes=list(c.routes),
                voice_tools=list(c.voice_tools),
                on_effect=c.on_effect,
                off_effect=c.off_effect,
                onboarding_setup=c.onboarding_setup,
                config_keys=sorted(c.config_keys),
            )
            for c in CAPABILITIES
        ],
    )


async def load_snapshot(db: AsyncSession, user: User) -> HostSnapshot:
    snap = HostSnapshot(user=user)

    properties = (
        await db.execute(
            select(
                Property.exophone,
                Property.twilio_number,
                Property.base_price,
                Property.exact_airbnb_pricing,
                Property.airbnb_listing_id,
                Property.ical_url,
                Property.photos,
                Property.faq,
            ).where(Property.user_id == user.id)
        )
    ).all()
    snap.property_count = len(properties)
    for p in properties:
        snap.has_property_phone |= bool(p.exophone or p.twilio_number)
        snap.has_priceable_property |= bool(p.exact_airbnb_pricing or (p.base_price or 0) > 0)
        snap.has_ical_link |= bool(p.ical_url)
        snap.has_live_pricing_property |= bool(p.exact_airbnb_pricing and p.airbnb_listing_id)
        snap.has_property_photos |= bool(p.photos)
        snap.has_legacy_property_faq |= bool(p.faq)

    snap.technician_count = await db.scalar(
        select(func.count())
        .select_from(Technician)
        .join(Property, Technician.property_id == Property.id)
        .where(Property.user_id == user.id)
    ) or 0
    snap.verified_faq_count = await db.scalar(
        select(func.count()).select_from(FaqEntry).where(FaqEntry.user_id == user.id, FaqEntry.status == "verified")
    ) or 0
    snap.approved_rule_count = await db.scalar(
        select(func.count())
        .select_from(NegotiationRule)
        .where(NegotiationRule.host_id == user.id, NegotiationRule.status == "approved")
    ) or 0

    rows = (await db.scalars(select(HostCapability).where(HostCapability.user_id == user.id))).all()
    snap.rows = {row.capability_id: row for row in rows}
    return snap


def _live_in_backend(cap: Capability, snap: HostSnapshot) -> bool | None:
    if cap.live_requirement is None:
        return None
    return _REQUIREMENT_CHECKS[cap.live_requirement](snap)


def _stored_preference(cap: Capability, snap: HostSnapshot) -> bool:
    row = snap.rows.get(cap.id)
    return row.enabled if row is not None else cap.default_enabled


def is_enabled_in_snapshot(cap: Capability, snap: HostSnapshot) -> bool:
    if cap.activation == "core":
        return True
    if cap.activation == "bound":
        return bool(getattr(snap.user, cap.bound_user_field))
    # An advisory capability the backend is already running for this host
    # reads as enabled whatever the stored preference -- reporting it as
    # off would claim something is stopped that is still serving guests.
    if _live_in_backend(cap, snap):
        return True
    return _stored_preference(cap, snap)


def _integration_statuses(cap: Capability) -> list[IntegrationStatusOut]:
    out = []
    for ref in cap.integrations:
        label, check = _INTEGRATIONS[ref.id]
        out.append(IntegrationStatusOut(id=ref.id, label=label, required=ref.required, connected=check()))
    return out


def _missing_dependencies(cap: Capability, snap: HostSnapshot) -> list[str]:
    return [
        dep_id for dep_id in cap.hard_dependencies if not is_enabled_in_snapshot(get_capability(dep_id), snap)
    ]


def _enabled_dependents(cap: Capability, snap: HostSnapshot) -> list[Capability]:
    return [d for d in dependents_of(cap.id) if d.activation != "core" and is_enabled_in_snapshot(d, snap)]


def resolve_status(cap: Capability, snap: HostSnapshot) -> CapabilityStatusOut:
    integrations = _integration_statuses(cap)
    missing_integrations = [i for i in integrations if i.required and not i.connected]
    available = not missing_integrations
    enabled = is_enabled_in_snapshot(cap, snap)
    live = _live_in_backend(cap, snap)
    requirements = [
        RequirementOut(
            id=r.id,
            label=r.label,
            hard=r.hard,
            met=_REQUIREMENT_CHECKS[r.id](snap),
            action_label=r.action_label,
            action_route=r.action_route,
        )
        for r in cap.requirements
    ]
    missing_deps = _missing_dependencies(cap, snap)
    row = snap.rows.get(cap.id)

    if not available:
        state = "unavailable"
    elif not enabled:
        # "disabled" = the host (or the bound column) explicitly says off;
        # "available" = never chosen and off by default.
        state = "disabled" if cap.activation == "bound" or row is not None else "available"
    elif missing_deps or any(r.hard and not r.met for r in requirements):
        state = "needs_setup"
    else:
        state = "ready"

    enable_blocked: str | None = None
    if not enabled:
        if not available:
            names = ", ".join(i.label for i in missing_integrations)
            enable_blocked = f"Not available yet: {names} isn't connected for your workspace."
        elif missing_deps:
            names = ", ".join(get_capability(d).name for d in missing_deps)
            enable_blocked = f"Turn on {names} first."

    disable_blocked: str | None = None
    if cap.activation == "core":
        disable_blocked = "Always on -- other features and guest safety nets rely on it."
    elif enabled:
        if live:
            disable_blocked = cap.live_block_reason
        else:
            dependents = _enabled_dependents(cap, snap)
            if dependents:
                names = ", ".join(d.name for d in dependents)
                disable_blocked = f"{names} depends on this. Turn that off first."

    return CapabilityStatusOut(
        id=cap.id,
        name=cap.name,
        description=cap.description,
        benefit=cap.benefit,
        group=cap.group,
        activation=cap.activation,
        enforcement=cap.enforcement,
        selectable=cap.selectable,
        available=available,
        enabled=enabled,
        state=state,
        setup_state=row.setup_state if row is not None else "not_started",
        live_in_backend=live,
        requirements=requirements,
        missing_dependencies=missing_deps,
        integrations=integrations,
        can_enable=not enabled and enable_blocked is None,
        enable_blocked_reason=enable_blocked,
        can_disable=enabled and disable_blocked is None,
        disable_blocked_reason=disable_blocked if enabled else None,
        effect=cap.on_effect if enabled else cap.off_effect,
        on_effect=cap.on_effect,
        off_effect=cap.off_effect,
        routes=list(cap.routes),
        onboarding_setup=cap.onboarding_setup,
        config=dict(row.config or {}) if row is not None else {},
        updated_at=row.updated_at if row is not None else None,
    )


async def host_capabilities(db: AsyncSession, user: User) -> HostCapabilitiesOut:
    snap = await load_snapshot(db, user)
    return HostCapabilitiesOut(
        groups=[CapabilityGroupOut(id=g.id, name=g.name, description=g.description) for g in GROUPS],
        capabilities=[resolve_status(c, snap) for c in CAPABILITIES],
    )


async def usable_capability_ids(db: AsyncSession, user: User) -> set[str]:
    """Capabilities that are enabled AND available (required integrations
    connected) for the host -- the eligibility input for navigation and
    Overview widgets (app/services/ui_preferences_service.py), so layout
    never re-derives capability state on its own."""
    snap = await load_snapshot(db, user)
    usable = set()
    for cap in CAPABILITIES:
        status_ = resolve_status(cap, snap)
        if status_.enabled and status_.available:
            usable.add(cap.id)
    return usable


def _require(capability_id: str) -> Capability:
    cap = get_capability(capability_id)
    if cap is None:
        raise CapabilityNotFoundError(capability_id)
    return cap


def _upsert_row(db: AsyncSession, snap: HostSnapshot, cap: Capability) -> HostCapability:
    row = snap.rows.get(cap.id)
    if row is None:
        row = HostCapability(
            user_id=snap.user.id,
            capability_id=cap.id,
            enabled=is_enabled_in_snapshot(cap, snap),
            setup_state="not_started",
            config={},
        )
        db.add(row)
        snap.rows[cap.id] = row
    return row


def _apply_enabled(db: AsyncSession, snap: HostSnapshot, cap: Capability, enabled: bool) -> None:
    """Validates and stages one activation change (caller commits)."""
    status_ = resolve_status(cap, snap)
    if enabled == status_.enabled:
        if cap.activation == "preference":
            # Still record the explicit choice (e.g. "keep it on" during
            # onboarding) so it survives a later change of default.
            _upsert_row(db, snap, cap).enabled = enabled
        return
    if enabled and not status_.can_enable:
        raise CapabilityChangeBlockedError(status_.enable_blocked_reason or f"{cap.name} can't be turned on.")
    if not enabled and not status_.can_disable:
        raise CapabilityChangeBlockedError(status_.disable_blocked_reason or f"{cap.name} can't be turned off.")

    now = datetime.now(timezone.utc)
    if cap.activation == "bound":
        setattr(snap.user, cap.bound_user_field, enabled)
    row = _upsert_row(db, snap, cap)
    # For "bound" capabilities this mirrors the column for timestamps only
    # -- resolution always reads the column itself.
    row.enabled = enabled
    if enabled:
        row.enabled_at = now
    else:
        row.disabled_at = now


async def update_capability(
    db: AsyncSession, user: User, capability_id: str, payload: CapabilityUpdate
) -> HostCapabilitiesOut:
    cap = _require(capability_id)
    snap = await load_snapshot(db, user)

    if payload.config is not None:
        unknown = sorted(set(payload.config) - set(cap.config_keys))
        if unknown:
            raise CapabilityConfigError(f"Unknown config for {cap.name}: {', '.join(unknown)}")
        for key, value in payload.config.items():
            expected = cap.config_keys[key]
            if value is not None and not isinstance(value, expected):
                raise CapabilityConfigError(f"{key} must be a {expected.__name__}")

    if payload.enabled is not None:
        _apply_enabled(db, snap, cap, payload.enabled)
    if payload.setup_state is not None:
        _upsert_row(db, snap, cap).setup_state = payload.setup_state
    if payload.config:
        row = _upsert_row(db, snap, cap)
        # Reassigned, not mutated in place -- JSONB columns don't track
        # in-place dict mutation.
        row.config = {**(row.config or {}), **payload.config}

    await db.commit()
    await db.refresh(user)
    return await host_capabilities(db, user)


async def apply_selection(db: AsyncSession, user: User, selected: set[str]) -> list[str]:
    """Onboarding's "choose capabilities" step: every selectable capability
    is turned on if selected, off otherwise, through the same validation as
    Settings. A change that's blocked (e.g. turning off something still live
    in the backend, or one that isn't available) is skipped and explained
    instead of failing the whole step. Caller commits."""
    snap = await load_snapshot(db, user)
    notes: list[str] = []
    # Enables first, so a newly selected dependency is on before anything
    # that needs it is validated; disables last, for the mirror reason.
    ordered = sorted((c for c in CAPABILITIES if c.selectable), key=lambda c: c.id not in selected)
    for cap in ordered:
        try:
            _apply_enabled(db, snap, cap, cap.id in selected)
        except CapabilityChangeBlockedError as exc:
            notes.append(f"{cap.name}: {exc}")
    return notes


async def is_capability_enabled(user_id: uuid.UUID, capability_id: str) -> bool:
    """Cheap runtime check for code paths that enforce a capability (today:
    technician dispatch). Uses its own session so a failure here can never
    poison the caller's transaction on a live call, and fails open to the
    registry default -- i.e. pre-capability behavior -- on any error."""
    cap = get_capability(capability_id)
    if cap is None:
        return True
    if cap.activation == "core":
        return True
    try:
        async with AsyncSessionLocal() as session:
            if cap.activation == "bound":
                value = await session.scalar(select(getattr(User, cap.bound_user_field)).where(User.id == user_id))
                return cap.default_enabled if value is None else bool(value)
            stored = await session.scalar(
                select(HostCapability.enabled).where(
                    HostCapability.user_id == user_id, HostCapability.capability_id == capability_id
                )
            )
            return cap.default_enabled if stored is None else bool(stored)
    except Exception:  # noqa: BLE001 - must never break a live call
        logger.exception("capability lookup failed for %s/%s -- failing open", user_id, capability_id)
        return cap.default_enabled
