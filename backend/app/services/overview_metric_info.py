"""What each Overview number means, how it is calculated and where it comes
from -- the text behind the hover info on every Overview card.

GET /analytics/overview returns this alongside the numbers, so the card and its
explanation come from one response and cannot drift apart. Metrics the Analytics
page already defines (occupancy, revenue, ADR, upcoming revenue, booking funnel)
reuse that registry's wording instead of restating it; the rest are defined here.
Keep each `formula` in step with the function in analytics_service.py that
computes the number.

window:
  "period" -- follows the date range picked in the header.
  "now"    -- current state; the date range does not change it.
"""

from dataclasses import asdict, dataclass
from typing import Literal

from app.services.analytics_widget_registry import METRICS_BY_ID

Window = Literal["period", "now"]


@dataclass(frozen=True)
class OverviewMetricInfo:
    id: str
    label: str
    definition: str
    formula: str
    source: str
    window: Window


def _from_registry(metric_id: str, source: str, window: Window) -> OverviewMetricInfo:
    metric = METRICS_BY_ID[metric_id]
    return OverviewMetricInfo(metric_id, metric.label, metric.description, metric.formula, source, window)


OVERVIEW_METRIC_INFO: tuple[OverviewMetricInfo, ...] = (
    _from_registry("revenue", "Bookings (confirmed final price)", "period"),
    _from_registry("upcoming_revenue", "Bookings (confirmed final price)", "now"),
    _from_registry("occupancy", "Bookings and blocked dates on your property calendars", "period"),
    _from_registry("adr", "Bookings (confirmed final price)", "period"),
    OverviewMetricInfo(
        "leads",
        "Leads",
        "Guests Mira captured as leads in this period.",
        "Leads created in the period. Open = still open or contacted. Hot = hot or very hot. "
        "Browser test calls are left out unless you include them.",
        "Leads",
        "period",
    ),
    OverviewMetricInfo(
        "open_actions",
        "Open actions",
        "Things you still have to do after a call: escalations, transfers, follow-ups and payments.",
        "Count of tickets with status open, right now. Escalations are the tickets raised by an escalation.",
        "Tickets",
        "now",
    ),
    OverviewMetricInfo(
        "resolution_rate",
        "Resolution rate",
        "Share of finished conversations that left nothing open for you.",
        "Resolved conversations ÷ finished conversations. A conversation is resolved when every ticket it raised "
        "is done or dismissed, every question Mira couldn't answer on it has since been answered, and any "
        "escalation has a closed ticket. Conversations still in progress are not counted.",
        "Call sessions, tickets and unanswered questions",
        "period",
    ),
    _from_registry("booking_funnel", "Mira conversations, leads and confirmed bookings", "period"),
    OverviewMetricInfo(
        "recent_calls",
        "Recent call activity",
        "The latest calls in this period.",
        "The 5 most recent calls by start time. AI summary and transcript are stored with each call.",
        "Call sessions",
        "period",
    ),
    OverviewMetricInfo(
        "needs_attention",
        "Needs your attention",
        "Work waiting on you.",
        "Open tickets, plus bookings waiting for you to confirm a price or a Mira match.",
        "Tickets and bookings",
        "now",
    ),
    OverviewMetricInfo(
        "unanswered_questions",
        "Questions Mira couldn't answer",
        "Guest questions Mira had no verified answer for.",
        "Pending unanswered questions, grouped when the text is identical. Answering one adds it to your FAQ.",
        "Unanswered questions",
        "now",
    ),
)

OVERVIEW_METRIC_INFO_BY_ID: dict[str, OverviewMetricInfo] = {m.id: m for m in OVERVIEW_METRIC_INFO}


def as_payload() -> dict[str, dict]:
    """{metric id: {label, definition, formula, source, window}} for the response."""
    return {m.id: {k: v for k, v in asdict(m).items() if k != "id"} for m in OVERVIEW_METRIC_INFO}
