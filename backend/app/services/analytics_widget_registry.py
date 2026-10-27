"""Analytics page building blocks: every metric tile and panel on the page,
mapped onto the capability registry, each with the plain-language
description and formula shown under its info icon.

Descriptions and formulas mirror app/services/analytics_service.py -- change
them together. The page layout itself (order, size, hidden metrics, and the
host's own headings) is a per-user preference stored by
ui_preferences_service under the "analytics_widgets" key; showing or hiding
a metric never changes what the analytics endpoint computes.

A metric is available when at least one of its capabilities is enabled.
"""

from dataclasses import dataclass
from typing import Literal

MetricKind = Literal["tile", "panel"]
LayoutSize = Literal["sm", "md", "half", "full"]

_TILE_SIZES: tuple[LayoutSize, ...] = ("sm", "md")
_PANEL_SIZES: tuple[LayoutSize, ...] = ("half", "full")


@dataclass(frozen=True)
class AnalyticsMetric:
    id: str
    label: str
    description: str
    formula: str
    kind: MetricKind
    capabilities: tuple[str, ...]
    default_size: LayoutSize

    @property
    def sizes(self) -> tuple[LayoutSize, ...]:
        return _TILE_SIZES if self.kind == "tile" else _PANEL_SIZES


def _tile(id_: str, label: str, description: str, formula: str, *capabilities: str) -> AnalyticsMetric:
    return AnalyticsMetric(id_, label, description, formula, "tile", capabilities, "sm")


def _panel(id_: str, label: str, description: str, formula: str, size: LayoutSize, *capabilities: str) -> AnalyticsMetric:
    return AnalyticsMetric(id_, label, description, formula, "panel", capabilities, size)


ANALYTICS_METRICS: tuple[AnalyticsMetric, ...] = (
    _panel(
        "needs_confirmation",
        "Booking details need confirmation",
        "Bookings waiting for you to confirm a price or whether Mira won them.",
        "Synced or manual bookings with a missing price or an unconfirmed Mira match.",
        "full",
        "booking_reconciliation",
    ),
    # Portfolio performance
    _tile(
        "occupancy",
        "Occupancy",
        "Share of available nights that were booked.",
        "Booked nights ÷ available nights. Available nights = properties × days in range − blocked nights.",
        "analytics_reporting",
    ),
    _tile(
        "revenue",
        "Revenue",
        "Confirmed booking revenue earned in this period.",
        "Σ confirmed final price × (nights in this period ÷ stay length), for stays overlapping the period.",
        "analytics_reporting",
    ),
    _tile(
        "adr",
        "ADR",
        "Average daily rate: what a booked night earned.",
        "Revenue ÷ booked nights with a confirmed price.",
        "analytics_reporting",
    ),
    _tile(
        "revpar",
        "RevPAR",
        "Revenue per available night, booked or not.",
        "Revenue ÷ available nights.",
        "analytics_reporting",
    ),
    _tile(
        "upcoming_revenue",
        "Upcoming revenue",
        "Revenue from confirmed stays that haven't finished yet.",
        "Σ confirmed final price of reservations checking out after today.",
        "analytics_reporting",
    ),
    _panel(
        "booking_funnel",
        "Booking funnel",
        "How Mira conversations progress to bookings.",
        "Conversations at each of six stages, from first call to confirmed booking; each shows its share of the previous stage.",
        "half",
        "lead_capture",
        "analytics_reporting",
    ),
    _panel(
        "guest_intent",
        "What guests ask about",
        "The topics guests raised on calls.",
        "Conversations mentioning each topic ÷ conversations with recorded questions.",
        "half",
        "knowledge_faq",
        "analytics_reporting",
    ),
    # Mira impact
    _tile(
        "attributed_bookings",
        "Mira-attributed bookings",
        "Bookings you confirmed came from a Mira conversation.",
        "Count of bookings with a confirmed Mira match.",
        "booking_reconciliation",
    ),
    _tile(
        "attributed_revenue",
        "Mira-attributed revenue",
        "Revenue from bookings you confirmed came from Mira.",
        "Σ confirmed final price of confirmed Mira-attributed bookings.",
        "booking_reconciliation",
    ),
    _tile(
        "after_hours_enquiries",
        "After-hours booking enquiries",
        "Booking enquiries Mira handled outside your own call hours.",
        "Booking-related calls that started while Mira, not you, was answering (outside your host call hours).",
        "analytics_reporting",
    ),
    _tile(
        "revenue_recovered",
        "Revenue recovered",
        "Revenue from guests Mira re-engaged after a busy line.",
        "Σ confirmed price of Mira-attributed bookings whose lead came from busy-call recovery.",
        "busy_call_recovery",
    ),
    _tile(
        "resolved_by_mira",
        "Resolved by Mira",
        "Calls Mira completed without escalating or transferring to you.",
        "Completed calls − calls escalated − calls transferred to you.",
        "analytics_reporting",
    ),
    _tile(
        "host_escalations",
        "Host escalations",
        "Calls Mira escalated to you.",
        "Count of calls with an escalation to the host.",
        "host_handoff",
    ),
    # Pricing & negotiation
    _tile(
        "initial_quote",
        "Initial quote / night",
        "Average first price Mira quoted, per night.",
        "Mean of (initial quote ÷ nights).",
        "pricing_quotes",
    ),
    _tile(
        "negotiated_price",
        "Negotiated price / night",
        "Average final offer in negotiations, per night.",
        "Mean of (each negotiation's last offer ÷ nights).",
        "negotiation",
    ),
    _tile(
        "final_price",
        "Final booking price / night",
        "Average confirmed price of stays checking in this period, per night.",
        "Mean of (confirmed final price ÷ nights).",
        "pricing_quotes",
    ),
    _tile(
        "negotiation_conversion",
        "Negotiation → booking",
        "Negotiations that ended in a booking.",
        "Negotiated conversations that converted ÷ negotiated conversations.",
        "negotiation",
    ),
    _tile(
        "avg_discount",
        "Average discount",
        "Average discount given in negotiations.",
        "Mean of (1 − last offer ÷ undiscounted price).",
        "negotiation",
    ),
    _tile(
        "price_objections",
        "Price objections",
        "Booking calls where the guest said the price was too high.",
        "Booking-related calls tagged price-too-high ÷ booking-related calls.",
        "pricing_quotes",
    ),
)

METRICS_BY_ID: dict[str, AnalyticsMetric] = {m.id: m for m in ANALYTICS_METRICS}


def _heading(id_: str, title: str, subtitle: str | None = None) -> dict:
    return {"type": "heading", "id": id_, "title": title, "subtitle": subtitle}


def _metric(id_: str) -> dict:
    return {"type": "metric", "id": id_, "size": METRICS_BY_ID[id_].default_size, "hidden": False}


# The page as it looked before it became customizable: the old section
# titles/descriptions are now ordinary, editable heading entries.
DEFAULT_LAYOUT: tuple[dict, ...] = (
    _metric("needs_confirmation"),
    _heading("portfolio", "Portfolio performance", "How your listings are booking and earning."),
    *(_metric(m) for m in ("occupancy", "revenue", "adr", "revpar", "upcoming_revenue")),
    _metric("booking_funnel"),
    _metric("guest_intent"),
    _heading("impact", "Mira impact", "Only bookings you've confirmed as Mira bookings count here."),
    *(
        _metric(m)
        for m in (
            "attributed_bookings",
            "attributed_revenue",
            "after_hours_enquiries",
            "revenue_recovered",
            "resolved_by_mira",
            "host_escalations",
        )
    ),
    _heading("pricing", "Pricing & negotiation", "Prices compared per night."),
    *(
        _metric(m)
        for m in (
            "initial_quote",
            "negotiated_price",
            "final_price",
            "negotiation_conversion",
            "avg_discount",
            "price_objections",
        )
    ),
)

MAX_HEADINGS = 20
