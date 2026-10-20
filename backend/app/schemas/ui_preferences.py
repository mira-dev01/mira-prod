from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

WidgetSize = Literal["half", "full"]


class NavItemOut(BaseModel):
    id: str
    label: str
    href: str
    placement: Literal["pinned_top", "movable", "pinned_bottom"]
    hideable: bool
    # Eligibility (from the Phase 1 capability state) -- unavailable items
    # are listed so Settings can explain them, but never rendered as links.
    available: bool
    hidden: bool
    capabilities: list[str]
    unavailable_reason: str | None = None


class NavigationPreferencesOut(BaseModel):
    # Resolved order: pinned_top, movable (host order), pinned_bottom.
    items: list[NavItemOut]
    revision: int
    is_default: bool
    updated_at: datetime | None


class NavigationPreferencesUpdate(BaseModel):
    # Movable destination ids in the desired order. Pinned ids are ignored;
    # available ids left out keep their previous position.
    order: list[str] = Field(max_length=64)
    hidden: list[str] = Field(default_factory=list, max_length=64)
    # The revision this edit was based on (0 = never saved). A mismatch
    # means another tab/device saved first -> 409, nothing overwritten.
    expected_revision: int = Field(ge=0)


class WidgetOut(BaseModel):
    id: str
    name: str
    description: str
    capabilities: list[str]
    sizes: list[WidgetSize]
    size: WidgetSize
    hidden: bool
    hideable: bool
    default_visible: bool
    available: bool
    section: Literal["summary", "live", "details"]
    unavailable_reason: str | None = None


class OverviewLayoutOut(BaseModel):
    widgets: list[WidgetOut]
    revision: int
    is_default: bool
    updated_at: datetime | None


class WidgetLayoutEntry(BaseModel):
    id: str
    size: WidgetSize
    hidden: bool = False


class OverviewLayoutUpdate(BaseModel):
    # Available widgets in the desired order. Unavailable widgets keep
    # their saved settings untouched.
    widgets: list[WidgetLayoutEntry] = Field(max_length=64)
    expected_revision: int = Field(ge=0)


# ── Analytics page (metric tiles/panels + the host's own headings) ─────

AnalyticsSize = Literal["sm", "md", "half", "full"]


class AnalyticsEntryOut(BaseModel):
    """One row of the Analytics layout: a metric (tile or panel) or a
    host-written heading. Metric-only fields are None on headings and vice
    versa."""

    type: Literal["metric", "heading"]
    id: str
    # heading
    title: str | None = None
    subtitle: str | None = None
    # metric
    label: str | None = None
    description: str | None = None
    formula: str | None = None
    kind: Literal["tile", "panel"] | None = None
    sizes: list[AnalyticsSize] = []
    size: AnalyticsSize | None = None
    hidden: bool = False
    available: bool = True
    unavailable_reason: str | None = None


class AnalyticsLayoutOut(BaseModel):
    entries: list[AnalyticsEntryOut]
    revision: int
    is_default: bool
    updated_at: datetime | None


class AnalyticsEntryIn(BaseModel):
    type: Literal["metric", "heading"]
    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")
    size: AnalyticsSize | None = None
    hidden: bool = False
    title: str | None = Field(default=None, max_length=80)
    subtitle: str | None = Field(default=None, max_length=160)


class AnalyticsLayoutUpdate(BaseModel):
    # Every entry in display order: available metrics (shown or hidden) and
    # headings. Unavailable metrics keep their saved settings untouched.
    entries: list[AnalyticsEntryIn] = Field(max_length=128)
    expected_revision: int = Field(ge=0)
