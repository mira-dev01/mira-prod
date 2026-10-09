"""Navigation and Overview-widget layout preferences.

Layout is stored separately from everything Phase 1 owns (capability
activation/config, onboarding) and never feeds back into it: hiding a page
or a widget changes what the dashboard *shows*, never what Mira *does*.
Eligibility is read from capability_service (the single source of truth for
capability state), never re-derived here.

Saved layouts are always resolved against the current registries and
capability state on read:
- ids the registry no longer knows are dropped;
- new registry entries appear at their default position;
- entries whose capabilities are currently off are kept in the stored
  document (position, size, hidden flag) but reported unavailable, so
  turning the capability back on restores them exactly where they were.
"""

import logging
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.user_ui_preference import UserUiPreference
from app.schemas.ui_preferences import (
    NavigationPreferencesOut,
    NavigationPreferencesUpdate,
    NavItemOut,
    OverviewLayoutOut,
    OverviewLayoutUpdate,
    WidgetOut,
)
from app.services import capability_service
from app.services.capability_registry import get_capability
from app.services.navigation_registry import NAV_BY_ID, NAV_DESTINATIONS, NavDestination
from app.services.overview_widget_registry import OVERVIEW_WIDGETS, WIDGETS_BY_ID, OverviewWidget

logger = logging.getLogger(__name__)

NAV_KEY = "navigation"
WIDGETS_KEY = "overview_widgets"
SCHEMA_VERSION = 1


class PreferenceValidationError(ValueError):
    """422 -- the request names something it isn't allowed to."""


class PreferenceConflictError(Exception):
    """409 -- saved elsewhere since this edit started."""


class PreferencePersistenceError(Exception):
    """503 -- the database write failed; nothing was changed."""


# ── Shared helpers ───────────────────────────────────────────────────────


def fill_missing(base: list[str], reference: Iterable[str], only: set[str] | None = None) -> list[str]:
    """Returns `base` plus every id from `reference` that `base` lacks (or,
    with `only`, every such id that is also in `only`), each inserted right
    after its nearest preceding `reference` neighbour already present.
    Used both to slot new registry entries into a saved layout at their
    default position and to put retained (currently unavailable) entries
    back where the host last had them."""
    result = list(dict.fromkeys(base))
    ref = list(reference)
    for idx, item in enumerate(ref):
        if item in result or (only is not None and item not in only):
            continue
        position = 0
        for previous in reversed(ref[:idx]):
            if previous in result:
                position = result.index(previous) + 1
                break
        result.insert(position, item)
    return result


def _reject_duplicates(ids: list[str], what: str) -> None:
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise PreferenceValidationError(f"Duplicate {what}: {', '.join(dupes)}")


def _unavailable_reason(capability_ids: tuple[str, ...]) -> str:
    names = [get_capability(c).name for c in capability_ids if get_capability(c)]
    if not names:
        return "Not available on your account."
    return f"Turn on {' or '.join(names)} in Settings > Features & modules to use this."


async def _get_row(db: AsyncSession, user: User, key: str, *, for_update: bool = False) -> UserUiPreference | None:
    stmt = select(UserUiPreference).where(UserUiPreference.user_id == user.id, UserUiPreference.key == key)
    if for_update:
        stmt = stmt.with_for_update()
    return await db.scalar(stmt)


async def _write(db: AsyncSession, user: User, key: str, row: UserUiPreference | None, expected: int, data: dict):
    current = row.revision if row is not None else 0
    if expected != current:
        raise PreferenceConflictError(
            "Your layout was changed in another tab or device. Reload to see the latest version, then try again."
        )
    if row is None:
        row = UserUiPreference(user_id=user.id, key=key, schema_version=SCHEMA_VERSION, revision=1, data=data)
        db.add(row)
    else:
        row.data = data
        row.schema_version = SCHEMA_VERSION
        row.revision = current + 1
    try:
        await db.commit()
    except IntegrityError as exc:
        # Two first-ever saves raced on the (user_id, key) unique constraint.
        await db.rollback()
        raise PreferenceConflictError(
            "Your layout was changed in another tab or device. Reload to see the latest version, then try again."
        ) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("saving %s preferences failed for %s", key, user.id)
        raise PreferencePersistenceError("Couldn't save your layout right now. Nothing was changed.") from exc
    await db.refresh(row)
    return row


async def _reset(db: AsyncSession, user: User, key: str) -> None:
    row = await _get_row(db, user, key, for_update=True)
    if row is not None:
        await db.delete(row)
        await db.commit()


# ── Navigation ───────────────────────────────────────────────────────────

_MOVABLE_DEFAULT = [d.id for d in NAV_DESTINATIONS if d.placement == "movable"]


def _nav_available(dest: NavDestination, usable: set[str]) -> bool:
    return dest.independent or any(c in usable for c in dest.capabilities)


def _stored_nav(row: UserUiPreference | None) -> tuple[list[str], set[str]]:
    data = (row.data if row is not None else None) or {}
    order = [i for i in data.get("order", []) if i in NAV_BY_ID and NAV_BY_ID[i].placement == "movable"]
    hidden = {i for i in data.get("hidden", []) if i in NAV_BY_ID and NAV_BY_ID[i].hideable}
    return fill_missing(order, _MOVABLE_DEFAULT), hidden


def _resolve_nav(row: UserUiPreference | None, usable: set[str]) -> NavigationPreferencesOut:
    order, hidden = _stored_nav(row)
    ids = (
        [d.id for d in NAV_DESTINATIONS if d.placement == "pinned_top"]
        + order
        + [d.id for d in NAV_DESTINATIONS if d.placement == "pinned_bottom"]
    )
    items = []
    for dest_id in ids:
        dest = NAV_BY_ID[dest_id]
        available = _nav_available(dest, usable)
        items.append(
            NavItemOut(
                id=dest.id,
                label=dest.label,
                href=dest.href,
                placement=dest.placement,
                hideable=dest.hideable,
                available=available,
                hidden=dest.id in hidden,
                capabilities=list(dest.capabilities),
                unavailable_reason=None if available else _unavailable_reason(dest.capabilities),
            )
        )
    return NavigationPreferencesOut(
        items=items,
        revision=row.revision if row is not None else 0,
        is_default=row is None,
        updated_at=row.updated_at if row is not None else None,
    )


async def get_navigation(db: AsyncSession, user: User) -> NavigationPreferencesOut:
    usable = await capability_service.usable_capability_ids(db, user)
    return _resolve_nav(await _get_row(db, user, NAV_KEY), usable)


async def save_navigation(
    db: AsyncSession, user: User, payload: NavigationPreferencesUpdate
) -> NavigationPreferencesOut:
    _reject_duplicates(payload.order, "navigation items")
    _reject_duplicates(payload.hidden, "hidden items")
    usable = await capability_service.usable_capability_ids(db, user)

    order: list[str] = []
    for dest_id in payload.order:
        dest = NAV_BY_ID.get(dest_id)
        if dest is None:
            raise PreferenceValidationError(f"Unknown navigation item: {dest_id}")
        if dest.placement != "movable":
            continue  # Overview/Settings stay pinned -- position isn't the host's to change.
        if not _nav_available(dest, usable):
            raise PreferenceValidationError(f"{dest.label} isn't available on your account.")
        order.append(dest_id)

    hidden: set[str] = set()
    for dest_id in payload.hidden:
        dest = NAV_BY_ID.get(dest_id)
        if dest is None:
            raise PreferenceValidationError(f"Unknown navigation item: {dest_id}")
        if not dest.hideable:
            raise PreferenceValidationError(f"{dest.label} always stays in the navigation.")
        if not _nav_available(dest, usable):
            raise PreferenceValidationError(f"{dest.label} isn't available on your account.")
        hidden.add(dest_id)

    row = await _get_row(db, user, NAV_KEY, for_update=True)
    previous_order, previous_hidden = _stored_nav(row)
    # Anything not in this request (currently unavailable entries, or
    # available ones the client left out) keeps its previous position and
    # hidden flag rather than being reset.
    new_order = fill_missing(order, previous_order)
    # Hidden flags of currently unavailable destinations can't be edited
    # (they aren't offered), so carry them over unchanged.
    retained_hidden = {i for i in previous_hidden if not _nav_available(NAV_BY_ID[i], usable)}
    data = {"order": new_order, "hidden": sorted(hidden | retained_hidden)}

    row = await _write(db, user, NAV_KEY, row, payload.expected_revision, data)
    return _resolve_nav(row, usable)


async def reset_navigation(db: AsyncSession, user: User) -> NavigationPreferencesOut:
    await _reset(db, user, NAV_KEY)
    return await get_navigation(db, user)


# ── Overview widgets ─────────────────────────────────────────────────────

_WIDGET_DEFAULT_ORDER = [w.id for w in OVERVIEW_WIDGETS]


def _widget_available(widget: OverviewWidget, usable: set[str]) -> bool:
    return any(c in usable for c in widget.capabilities)


def _stored_widgets(row: UserUiPreference | None) -> dict[str, dict]:
    """Ordered {id: {"size", "hidden"}} -- sanitized against the registry,
    with new registry widgets filled in at their default position."""
    data = (row.data if row is not None else None) or {}
    entries: dict[str, dict] = {}
    for entry in data.get("widgets", []):
        if not isinstance(entry, dict):
            continue
        widget = WIDGETS_BY_ID.get(entry.get("id"))
        if widget is None or widget.id in entries:
            continue
        size = entry.get("size") if entry.get("size") in widget.sizes else widget.default_size
        hidden = bool(entry.get("hidden")) if widget.hideable else False
        entries[widget.id] = {"size": size, "hidden": hidden}
    ordered = fill_missing(list(entries), _WIDGET_DEFAULT_ORDER)
    return {
        wid: entries.get(wid)
        or {"size": WIDGETS_BY_ID[wid].default_size, "hidden": not WIDGETS_BY_ID[wid].default_visible}
        for wid in ordered
    }


def _resolve_widgets(row: UserUiPreference | None, usable: set[str]) -> OverviewLayoutOut:
    widgets = []
    for wid, settings_ in _stored_widgets(row).items():
        widget = WIDGETS_BY_ID[wid]
        available = _widget_available(widget, usable)
        widgets.append(
            WidgetOut(
                id=widget.id,
                name=widget.name,
                description=widget.description,
                capabilities=list(widget.capabilities),
                sizes=list(widget.sizes),
                size=settings_["size"],
                hidden=settings_["hidden"],
                hideable=widget.hideable,
                default_visible=widget.default_visible,
                available=available,
                section=widget.section,
                unavailable_reason=None if available else _unavailable_reason(widget.capabilities),
            )
        )
    return OverviewLayoutOut(
        widgets=widgets,
        revision=row.revision if row is not None else 0,
        is_default=row is None,
        updated_at=row.updated_at if row is not None else None,
    )


async def get_overview_layout(db: AsyncSession, user: User) -> OverviewLayoutOut:
    usable = await capability_service.usable_capability_ids(db, user)
    return _resolve_widgets(await _get_row(db, user, WIDGETS_KEY), usable)


async def save_overview_layout(db: AsyncSession, user: User, payload: OverviewLayoutUpdate) -> OverviewLayoutOut:
    _reject_duplicates([w.id for w in payload.widgets], "widgets")
    usable = await capability_service.usable_capability_ids(db, user)

    submitted: dict[str, dict] = {}
    for entry in payload.widgets:
        widget = WIDGETS_BY_ID.get(entry.id)
        if widget is None:
            raise PreferenceValidationError(f"Unknown widget: {entry.id}")
        if not _widget_available(widget, usable):
            raise PreferenceValidationError(f"{widget.name} isn't available on your account.")
        if entry.size not in widget.sizes:
            raise PreferenceValidationError(
                f"{widget.name} supports these sizes only: {', '.join(widget.sizes)}."
            )
        if entry.hidden and not widget.hideable:
            raise PreferenceValidationError(f"{widget.name} can't be hidden.")
        submitted[entry.id] = {"size": entry.size, "hidden": entry.hidden}

    row = await _get_row(db, user, WIDGETS_KEY, for_update=True)
    previous = _stored_widgets(row)
    # Widgets not in this request (unavailable ones, or ones the client
    # omitted) keep their previous position and settings.
    order = fill_missing(list(submitted), list(previous))
    data = {"widgets": [{"id": wid, **(submitted.get(wid) or previous[wid])} for wid in order]}

    row = await _write(db, user, WIDGETS_KEY, row, payload.expected_revision, data)
    return _resolve_widgets(row, usable)


async def reset_overview_layout(db: AsyncSession, user: User) -> OverviewLayoutOut:
    await _reset(db, user, WIDGETS_KEY)
    return await get_overview_layout(db, user)
