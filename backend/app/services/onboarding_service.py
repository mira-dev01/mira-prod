"""Server-side onboarding progress.

Progress lives in host_onboarding (one row per host) so the dashboard can tell a host who has finished
onboarding from one who hasn't. Properties are not part of onboarding: a host may have one or a hundred, on
Airbnb or their own site, and they are imported together from Properties (or, later, a CRM sync).
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.host_onboarding import HostOnboarding
from app.models.property import Property
from app.models.user import User
from app.schemas.onboarding import OnboardingOut, OnboardingProgressUpdate
from app.services import capability_service
from app.services.capability_registry import CAPABILITIES, SELECTABLE_CAPABILITY_IDS

logger = logging.getLogger(__name__)

STEPS = ("profile", "capabilities", "setup", "review")


class OnboardingError(Exception):
    """Client error (400/409/422) with a host-readable message."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _default_selection() -> list[str]:
    return [c.id for c in CAPABILITIES if c.selectable and c.default_enabled]


async def _get_row(db: AsyncSession, user: User) -> HostOnboarding | None:
    return await db.scalar(select(HostOnboarding).where(HostOnboarding.user_id == user.id))


async def _infer_legacy(db: AsyncSession, user: User) -> HostOnboarding | None:
    """A host with no onboarding row who already has a working setup
    (properties or a Guest Call Number) predates this flow -- e.g. created
    by seed_demo.py or the old POST /auth/onboarding after the migration's
    backfill ran. Record them as onboarded rather than forcing them back
    through it."""
    has_property = await db.scalar(select(Property.id).where(Property.user_id == user.id).limit(1))
    if has_property is None and not (user.lead_exophone or user.twilio_lead_number):
        return None
    row = HostOnboarding(
        user_id=user.id,
        status="completed",
        source="legacy",
        current_step="done",
        completed_steps=[],
        selected_capabilities=[],
        data={},
        completed_at=datetime.now(timezone.utc),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def _to_out(row: HostOnboarding | None, notes: list[str] | None = None) -> OnboardingOut:
    if row is None:
        return OnboardingOut(
            status="not_started",
            source=None,
            current_step="profile",
            completed_steps=[],
            selected_capabilities=_default_selection(),
            completed_at=None,
        )
    return OnboardingOut(
        status=row.status,
        source=row.source,
        current_step=row.current_step,
        completed_steps=list(row.completed_steps or []),
        selected_capabilities=list(row.selected_capabilities or []),
        completed_at=row.completed_at,
        selection_notes=notes or [],
    )


async def get_state(db: AsyncSession, user: User) -> OnboardingOut:
    row = await _get_row(db, user)
    if row is None:
        row = await _infer_legacy(db, user)
    return _to_out(row)


async def _get_or_create_in_progress(db: AsyncSession, user: User) -> HostOnboarding:
    row = await _get_row(db, user)
    if row is None:
        row = HostOnboarding(
            user_id=user.id,
            status="in_progress",
            source="onboarding",
            current_step="profile",
            completed_steps=[],
            selected_capabilities=_default_selection(),
            data={},
        )
        db.add(row)
        await db.flush()
    return row


async def save_progress(db: AsyncSession, user: User, payload: OnboardingProgressUpdate) -> OnboardingOut:
    row = await _get_or_create_in_progress(db, user)
    if row.status == "completed":
        raise OnboardingError(409, "Onboarding is already complete -- manage features from Settings.")

    notes: list[str] = []
    if payload.selected_capabilities is not None:
        unknown = sorted(set(payload.selected_capabilities) - set(SELECTABLE_CAPABILITY_IDS))
        if unknown:
            raise OnboardingError(422, f"Not a selectable capability: {', '.join(unknown)}")
        selected = set(payload.selected_capabilities)
        notes = await capability_service.apply_selection(db, user, selected)
        # Keep registry order so the stored list is stable across saves.
        row.selected_capabilities = [cid for cid in SELECTABLE_CAPABILITY_IDS if cid in selected]
    if payload.completed_steps is not None:
        merged = set(row.completed_steps or []) | set(payload.completed_steps)
        row.completed_steps = [s for s in STEPS if s in merged]
    if payload.current_step is not None:
        row.current_step = payload.current_step

    await db.commit()
    await db.refresh(row)
    return _to_out(row, notes)


async def complete(db: AsyncSession, user: User) -> OnboardingOut:
    row = await _get_or_create_in_progress(db, user)
    if row.status == "completed":
        return _to_out(row)
    # The only essential requirement for reaching the dashboard: a profile.
    # Everything else (numbers, properties, technicians) has its own setup
    # action and must not block the whole experience.
    if not (user.name or "").strip():
        raise OnboardingError(400, "Add your name in the business profile step first.")
    row.status = "completed"
    row.current_step = "done"
    row.completed_steps = list(STEPS)
    row.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return _to_out(row)
