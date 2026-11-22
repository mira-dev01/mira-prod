"""The Overview's hover text: every card has a definition, formula and source,
and the metrics the Analytics page already defines reuse its wording."""

from app.services.analytics_widget_registry import METRICS_BY_ID
from app.services.overview_metric_info import OVERVIEW_METRIC_INFO, OVERVIEW_METRIC_INFO_BY_ID, as_payload


def test_every_metric_states_what_it_is_how_it_is_calculated_and_where_it_comes_from():
    for metric in OVERVIEW_METRIC_INFO:
        assert metric.label.strip(), metric.id
        assert metric.definition.strip(), metric.id
        assert metric.formula.strip(), metric.id
        assert metric.source.strip(), metric.id
        assert metric.window in ("period", "now"), metric.id


def test_metric_ids_are_unique():
    assert len(OVERVIEW_METRIC_INFO_BY_ID) == len(OVERVIEW_METRIC_INFO)


def test_shared_metrics_reuse_the_analytics_registry_wording():
    for metric_id in ("revenue", "upcoming_revenue", "occupancy", "adr", "booking_funnel"):
        shared = METRICS_BY_ID[metric_id]
        info = OVERVIEW_METRIC_INFO_BY_ID[metric_id]
        assert info.definition == shared.description and info.formula == shared.formula


def test_payload_is_keyed_by_id_without_repeating_it():
    payload = as_payload()
    assert set(payload) == set(OVERVIEW_METRIC_INFO_BY_ID)
    assert all("id" not in entry for entry in payload.values())
