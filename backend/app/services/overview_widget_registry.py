"""Overview widgets -- each one an existing dashboard component
(frontend/src/components/overview/widgets.tsx maps id -> component), mapped
onto the capability registry.

A widget is *available* when at least one of its capabilities is enabled
for the host. Unavailable widgets are never offered or rendered, but their
saved position/size/visibility is kept so re-enabling the capability puts
them back where the host had them. Showing or hiding a widget is purely a
layout preference: it never starts or stops a backend service, scheduled
job or data collection, and never changes what any analytics endpoint
returns.
"""

from dataclasses import dataclass
from typing import Literal

WidgetSize = Literal["half", "full"]


@dataclass(frozen=True)
class OverviewWidget:
    id: str
    name: str
    description: str
    capabilities: tuple[str, ...]
    default_visible: bool
    default_size: WidgetSize
    sizes: tuple[WidgetSize, ...]
    hideable: bool = True
    # Existing API sources the widget reads (informational; the widget
    # uses them exactly as the Overview page always has).
    data_sources: tuple[str, ...] = ()
    # Consecutive widgets of the same section render under one heading,
    # which reproduces the original Overview layout for the default order.
    section: Literal["summary", "live", "details"] = "details"


OVERVIEW_WIDGETS: tuple[OverviewWidget, ...] = (
    OverviewWidget(
        "portfolio_snapshot",
        "Portfolio snapshot",
        "Headline numbers for the selected period.",
        ("analytics_reporting",),
        default_visible=True,
        default_size="full",
        sizes=("full",),
        data_sources=("GET /analytics/overview",),
        section="summary",
    ),
    OverviewWidget(
        "needs_attention",
        "Needs your attention",
        "Escalations, booking reviews and other items waiting on you.",
        ("lead_capture", "booking_reconciliation", "analytics_reporting"),
        default_visible=True,
        default_size="full",
        sizes=("full",),
        data_sources=("GET /analytics/overview", "GET /bookings/reconciliation"),
        section="summary",
    ),
    OverviewWidget(
        "live_requests",
        "Live requests",
        "Open guest requests and escalations.",
        ("lead_capture", "host_handoff"),
        default_visible=True,
        default_size="half",
        sizes=("half", "full"),
        data_sources=("GET /leads",),
        section="live",
    ),
    OverviewWidget(
        "opportunities",
        "Opportunities",
        "Booking leads worth a follow-up, including busy-call recoveries.",
        ("lead_capture", "busy_call_recovery"),
        default_visible=True,
        default_size="half",
        sizes=("half", "full"),
        data_sources=("GET /leads",),
        section="live",
    ),
    OverviewWidget(
        "recent_calls",
        "Recent calls",
        "The latest calls in the selected period.",
        ("analytics_reporting", "guest_support_agent", "lead_agent"),
        default_visible=True,
        default_size="half",
        sizes=("half", "full"),
        data_sources=("GET /calls",),
        section="details",
    ),
    OverviewWidget(
        "unanswered_questions",
        "Unanswered questions",
        "Guest questions Mira couldn't answer yet.",
        ("knowledge_faq",),
        default_visible=True,
        default_size="half",
        sizes=("half", "full"),
        data_sources=("GET /faq/gaps",),
        section="details",
    ),
    # Not on the Overview by default -- the same card the Opportunities page
    # shows, offered as an add-on.
    OverviewWidget(
        "busy_call_recovery",
        "Busy-call recovery",
        "How many busy-line callers Mira recovered over WhatsApp.",
        ("busy_call_recovery",),
        default_visible=False,
        default_size="half",
        sizes=("half", "full"),
        data_sources=("GET /analytics/recovery",),
        section="details",
    ),
)

WIDGETS_BY_ID: dict[str, OverviewWidget] = {w.id: w for w in OVERVIEW_WIDGETS}
