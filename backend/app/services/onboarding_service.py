"""Server-side onboarding progress and the first-property import.

Progress lives in host_onboarding (one row per host) so a host can refresh,
close the browser or sign in elsewhere and resume. The first property's
Airbnb import is finished by the server, not the browser: before this,
POST /auth/onboarding only returned a Bright Data snapshot_id and the
property was created only if the browser kept polling GET
/properties/import-airbnb-urls/{id} -- closing the tab mid-scrape lost it.
Here the snapshot is recorded on the host's onboarding row and a background
poller (asyncio task, same fire-and-forget convention as the rest of the
backend -- there is no job queue) imports it through the exact same
parse/upsert path the Properties page uses. A GET after a restart re-spawns
the poller, and every step is idempotent: the same URL is never scraped
twice while one import is in flight or done, and the upsert itself dedupes
on (user_id, airbnb_listing_id).
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.integrations import bright_data_client
from app.integrations.bright_data_client import BrightDataError
from app.models.host_onboarding import HostOnboarding
from app.models.property import Property
from app.models.user import User
from app.schemas.onboarding import (
    FirstPropertyImportOut,
    FirstPropertyImportRequest,
    OnboardingOut,
    OnboardingProgressUpdate,
)
from app.services import capability_service
from app.services.capability_registry import CAPABILITIES, SELECTABLE_CAPABILITY_IDS

logger = logging.getLogger(__name__)

STEPS = ("profile", "capabilities", "setup", "review")

IMPORT_POLL_INTERVAL_SECONDS = 6.0
# After this, an import still not "ready" is marked failed so the host
# isn't left waiting forever -- they can retry or add the property manually.
IMPORT_MAX_AGE = timedelta(minutes=20)

# One poller per host per process. Keyed by user id; the value carries the
# snapshot it's polling so a retry with a new URL starts a fresh poller.
_pollers: dict[uuid.UUID, tuple[str, asyncio.Task]] = {}


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
            first_property=None,
            completed_at=None,
        )
    first_property = (row.data or {}).get("first_property")
    return OnboardingOut(
        status=row.status,
        source=row.source,
        current_step=row.current_step,
        completed_steps=list(row.completed_steps or []),
        selected_capabilities=list(row.selected_capabilities or []),
        first_property=FirstPropertyImportOut(**first_property) if first_property else None,
        completed_at=row.completed_at,
        selection_notes=notes or [],
    )


async def get_state(db: AsyncSession, user: User) -> OnboardingOut:
    row = await _get_row(db, user)
    if row is None:
        row = await _infer_legacy(db, user)
    if row is not None:
        _resume_import_if_needed(user.id, row)
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


# ── First-property import ────────────────────────────────────────────────


async def start_first_property_import(
    db: AsyncSession, user: User, payload: FirstPropertyImportRequest
) -> OnboardingOut:
    try:
        url = bright_data_client.normalize_listing_url(payload.airbnb_url)
    except BrightDataError as exc:
        raise OnboardingError(422, str(exc)) from exc
    ical_url = (payload.ical_url or "").strip() or None

    row = await _get_or_create_in_progress(db, user)
    if row.status == "completed":
        raise OnboardingError(409, "Onboarding is already complete -- import more properties from Properties.")

    existing = (row.data or {}).get("first_property")
    if existing and existing.get("airbnb_url") == url and existing.get("status") in ("importing", "completed"):
        # Double-click, refresh-and-resubmit, or a second tab: the same
        # listing is already importing or imported. Only the extras that
        # are applied after the import can still change.
        updated = {**existing, "ical_url": ical_url, "live_pricing": payload.live_pricing}
        row.data = {**(row.data or {}), "first_property": updated}
        await db.commit()
        if existing.get("status") == "completed" and existing.get("property_id"):
            await _apply_import_extras(user.id, uuid.UUID(existing["property_id"]), ical_url, payload.live_pricing)
        await db.refresh(row)
        _resume_import_if_needed(user.id, row)
        return _to_out(row)

    now = datetime.now(timezone.utc)
    record: dict = {
        "airbnb_url": url,
        "ical_url": ical_url,
        "live_pricing": payload.live_pricing,
        "status": "importing",
        "snapshot_id": None,
        "property_id": None,
        "property_name": None,
        "error": None,
        "requested_at": now.isoformat(),
        "finished_at": None,
    }
    try:
        record["snapshot_id"] = await bright_data_client.trigger_scrape([url])
    except BrightDataError as exc:
        # e.g. BRIGHT_DATA_API_KEY unset -- recorded, not raised: the host
        # can retry or add the property by hand without losing their place.
        record.update(status="failed", error=str(exc), finished_at=now.isoformat())

    row.data = {**(row.data or {}), "first_property": record}
    await db.commit()
    await db.refresh(row)
    _resume_import_if_needed(user.id, row)
    return _to_out(row)


def _resume_import_if_needed(user_id: uuid.UUID, row: HostOnboarding) -> None:
    record = (row.data or {}).get("first_property") or {}
    snapshot_id = record.get("snapshot_id")
    if record.get("status") != "importing" or not snapshot_id:
        return
    running = _pollers.get(user_id)
    if running is not None and running[0] == snapshot_id and not running[1].done():
        return
    _spawn_poller(user_id, snapshot_id)


def _spawn_poller(user_id: uuid.UUID, snapshot_id: str) -> None:
    task = asyncio.create_task(_poll_import(user_id, snapshot_id))
    _pollers[user_id] = (snapshot_id, task)

    def _forget(finished: asyncio.Task) -> None:
        current = _pollers.get(user_id)
        if current is not None and current[1] is finished:
            del _pollers[user_id]

    task.add_done_callback(_forget)


async def _update_import_record(user_id: uuid.UUID, snapshot_id: str, **changes) -> bool:
    """Applies changes to the first_property record only if it still refers
    to snapshot_id (a host may have retried with a different URL since)."""
    async with AsyncSessionLocal() as db:
        row = await db.scalar(select(HostOnboarding).where(HostOnboarding.user_id == user_id))
        record = ((row.data or {}).get("first_property") or {}) if row is not None else {}
        if row is None or record.get("snapshot_id") != snapshot_id:
            return False
        row.data = {**(row.data or {}), "first_property": {**record, **changes}}
        await db.commit()
        return True


async def _current_import_record(user_id: uuid.UUID) -> dict:
    async with AsyncSessionLocal() as db:
        row = await db.scalar(select(HostOnboarding).where(HostOnboarding.user_id == user_id))
        return ((row.data or {}).get("first_property") or {}) if row is not None else {}


async def _apply_import_extras(
    user_id: uuid.UUID, property_id: uuid.UUID, ical_url: str | None, live_pricing: bool
) -> None:
    """Applies the onboarding extras the old flow left to the browser to
    PATCH afterwards (iCal link) plus the live-pricing opt-in. Never
    switches live pricing OFF here -- that's a per-property decision on the
    Properties page."""
    async with AsyncSessionLocal() as db:
        property_ = await db.get(Property, property_id)
        if property_ is None or property_.user_id != user_id:
            return
        if ical_url:
            property_.ical_url = ical_url
        if live_pricing and property_.airbnb_listing_id:
            property_.exact_airbnb_pricing = True
        await db.commit()


async def process_ready_snapshot(user_id: uuid.UUID, snapshot_id: str) -> None:
    """Imports a ready snapshot and records the outcome. Split out from the
    poll loop so tests can drive it directly."""
    # Imported lazily: the parse/upsert path lives with the Properties
    # router (shared with its own poll endpoint), which imports services.
    from app.api.v1.properties import _listing_id_from_url, import_snapshot_records

    record = await _current_import_record(user_id)
    if record.get("snapshot_id") != snapshot_id:
        return

    records = await bright_data_client.get_snapshot_data(snapshot_id)
    results = await import_snapshot_records(user_id, records)
    imported = next((r for r in results if r.property is not None and r.status != "error"), None)
    property_id: uuid.UUID | None = imported.property.id if imported else None
    property_name: str | None = imported.property.name if imported else None

    if property_id is None:
        # A concurrent poller (another worker, or a pre-restart task) may
        # have won the (user_id, airbnb_listing_id) unique race -- the
        # property exists either way, so treat that as success.
        listing_id = _listing_id_from_url(record.get("airbnb_url") or "")
        async with AsyncSessionLocal() as db:
            existing = await db.scalar(
                select(Property).where(Property.user_id == user_id, Property.airbnb_listing_id == listing_id)
            )
            if existing is not None:
                property_id, property_name = existing.id, existing.name

    finished = datetime.now(timezone.utc).isoformat()
    if property_id is None:
        error = next((r.error for r in results if r.error), None) or "The listing couldn't be read from Airbnb."
        await _update_import_record(user_id, snapshot_id, status="failed", error=error, finished_at=finished)
        return

    await _apply_import_extras(user_id, property_id, record.get("ical_url"), bool(record.get("live_pricing")))
    await _update_import_record(
        user_id,
        snapshot_id,
        status="completed",
        property_id=str(property_id),
        property_name=property_name,
        error=None,
        finished_at=finished,
    )


async def _poll_import(user_id: uuid.UUID, snapshot_id: str) -> None:
    try:
        while True:
            record = await _current_import_record(user_id)
            if record.get("snapshot_id") != snapshot_id or record.get("status") != "importing":
                return
            requested_at = record.get("requested_at")
            if requested_at and datetime.now(timezone.utc) - datetime.fromisoformat(requested_at) > IMPORT_MAX_AGE:
                await _update_import_record(
                    user_id,
                    snapshot_id,
                    status="failed",
                    error="Importing took too long. Try again, or add the property manually.",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
                return

            try:
                job_status = await bright_data_client.get_snapshot_status(snapshot_id)
            except BrightDataError:
                # Transient -- keep polling until IMPORT_MAX_AGE.
                logger.warning("onboarding import status check failed for %s", snapshot_id, exc_info=True)
                job_status = "running"

            if job_status == "ready":
                await process_ready_snapshot(user_id, snapshot_id)
                return
            if job_status == "failed":
                await _update_import_record(
                    user_id,
                    snapshot_id,
                    status="failed",
                    error="Airbnb import failed. Try again, or add the property manually.",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
                return
            await asyncio.sleep(IMPORT_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - background task: record, never crash the process
        logger.exception("onboarding import poller crashed for user %s", user_id)
        await _update_import_record(
            user_id,
            snapshot_id,
            status="failed",
            error="Something went wrong importing this listing. Try again, or add it manually.",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
