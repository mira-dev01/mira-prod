"""Dashboard navigation destinations, mapped onto the capability registry
(app/services/capability_registry.py).

A destination is a page, not a capability: several capabilities surface on
one page, and one capability can surface on several pages. A destination is
*available* when it has an independent purpose (`independent=True`) or when
at least one of its capabilities is enabled for the host -- so a page only
disappears when nothing on it is left to use. Hiding an available
destination is purely a layout preference (app/services/
ui_preferences_service.py): it never disables a capability, and it is not
an authorization boundary -- every route's data is still protected by its
own backend endpoint.

Under today's capability model every destination is owned at least in part
by a core (always-on) capability or is independent, so none currently
becomes unavailable; the mechanism exists so a future optional-only page
reconciles correctly.

The frontend's icon map and error-state fallback list
(frontend/src/lib/navigation.ts) mirror these ids/hrefs --
tests/test_ui_preferences.py checks they stay in sync.

"Talk to Mira" is deliberately not a destination: it's an action gated by
the existing internal-org rule (User.is_internal_org), unchanged.
"""

from dataclasses import dataclass
from typing import Literal

Placement = Literal["pinned_top", "movable", "pinned_bottom"]


@dataclass(frozen=True)
class NavDestination:
    id: str
    label: str
    href: str
    capabilities: tuple[str, ...]
    # Has a purpose of its own regardless of optional capabilities (e.g.
    # Calendar still shows manually recorded bookings).
    independent: bool = False
    placement: Placement = "movable"
    hideable: bool = True


NAV_DESTINATIONS: tuple[NavDestination, ...] = (
    NavDestination(
        "overview",
        "Overview",
        "/dashboard",
        ("analytics_reporting", "lead_capture", "knowledge_faq", "booking_reconciliation"),
        independent=True,
        placement="pinned_top",
        hideable=False,
    ),
    NavDestination(
        "analytics",
        "Analytics",
        "/dashboard/analytics",
        ("analytics_reporting", "booking_reconciliation", "busy_call_recovery"),
    ),
    # Owns the property data every other feature reads -- reorderable but
    # never hideable.
    NavDestination(
        "properties", "Properties", "/dashboard/properties", ("property_management",), independent=True, hideable=False
    ),
    NavDestination(
        "ai_training",
        "AI Training",
        "/dashboard/properties/ai-training",
        ("guest_support_agent", "negotiation", "knowledge_faq"),
    ),
    NavDestination(
        "calendar",
        "Calendar",
        "/dashboard/calendar",
        ("calendar_sync", "booking_reconciliation"),
        independent=True,
    ),
    NavDestination("calls", "Calls", "/dashboard/calls", ("guest_support_agent", "lead_agent", "analytics_reporting")),
    NavDestination(
        "live_requests", "Live Requests", "/dashboard/leads", ("lead_capture", "busy_call_recovery", "host_handoff")
    ),
    NavDestination("opportunities", "Opportunities", "/dashboard/opportunities", ("lead_capture", "busy_call_recovery")),
    # The Guests page is one view of guest memory, not the capability.
    NavDestination("guests", "Guests", "/dashboard/guests", ("guest_memory",)),
    # FAQ navigation is where hosts curate knowledge; hiding it never stops
    # Mira answering from it (knowledge_faq is core).
    NavDestination("faq", "FAQ", "/dashboard/faq", ("knowledge_faq",)),
    NavDestination(
        "settings",
        "Settings",
        "/dashboard/settings",
        (),
        independent=True,
        placement="pinned_bottom",
        hideable=False,
    ),
)

NAV_BY_ID: dict[str, NavDestination] = {d.id: d for d in NAV_DESTINATIONS}
