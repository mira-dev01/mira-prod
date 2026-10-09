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
