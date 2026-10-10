import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.host_capability import HostCapability
from app.models.user import User
from app.models.user_ui_preference import UserUiPreference
from app.services import capability_registry, capability_service, ui_preferences_service
from app.services.navigation_registry import NAV_BY_ID, NAV_DESTINATIONS, NavDestination
from app.services.overview_widget_registry import OVERVIEW_WIDGETS, WIDGETS_BY_ID, OverviewWidget
from app.services.ui_preferences_service import fill_missing
from tests.conftest import auth_headers_for

NAV = "/api/v1/preferences/navigation"
WIDGETS = "/api/v1/preferences/overview-widgets"
FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

DEFAULT_NAV_IDS = [d.id for d in NAV_DESTINATIONS]
MOVABLE = [d.id for d in NAV_DESTINATIONS if d.placement == "movable"]


async def _other_user(db_session) -> User:
    user = User(email=f"o-{uuid.uuid4().hex[:8]}@example.com", name="Other")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _ids(body: dict) -> list[str]:
    return [i["id"] for i in body["items"]]


def _visible(body: dict) -> list[str]:
    return [i["id"] for i in body["items"] if i["available"] and not i["hidden"]]


def _widget_entries(body: dict) -> list[dict]:
    return [{"id": w["id"], "size": w["size"], "hidden": w["hidden"]} for w in body["widgets"] if w["available"]]


# ── Registry integrity + frontend contract ─────────────────────────────


def test_registries_reference_real_capabilities():
    for dest in NAV_DESTINATIONS:
        for cap in dest.capabilities:
            assert cap in capability_registry.CAPABILITIES_BY_ID, f"{dest.id} -> {cap}"
    for widget in OVERVIEW_WIDGETS:
        assert widget.capabilities, widget.id
        assert widget.default_size in widget.sizes
        for cap in widget.capabilities:
            assert cap in capability_registry.CAPABILITIES_BY_ID, f"{widget.id} -> {cap}"
    assert NAV_BY_ID["overview"].placement == "pinned_top" and not NAV_BY_ID["overview"].hideable
    assert NAV_BY_ID["settings"].placement == "pinned_bottom" and not NAV_BY_ID["settings"].hideable
    assert not NAV_BY_ID["properties"].hideable


def test_frontend_navigation_mirrors_registry():
    source = (FRONTEND / "lib" / "navigation.ts").read_text()
    fallback = re.findall(r'fallback\("([a-z_]+)", "[^"]+", "([^"]+)", "([a-z_]+)"', source)
    assert [(d.id, d.href, d.placement) for d in NAV_DESTINATIONS] == fallback
    for dest in NAV_DESTINATIONS:
        assert re.search(rf"^\s+{dest.id}: \w+,$", source, re.M), f"no icon for {dest.id}"


def test_frontend_widgets_mirror_registry():
    source = (FRONTEND / "components" / "overview" / "widgets.tsx").read_text()
    fallback = re.findall(r'fallback\("([a-z_]+)", "[^"]+", "([a-z]+)", "([a-z]+)"', source)
    assert [(w.id, w.section, w.default_size) for w in OVERVIEW_WIDGETS] == fallback
    for widget in OVERVIEW_WIDGETS:
        assert re.search(rf"^\s+{widget.id}: \(", source, re.M), f"no renderer for {widget.id}"


def test_fill_missing_inserts_after_nearest_predecessor():
    # b slots in after a, d after c (its nearest preceding reference neighbour).
    assert fill_missing(["c", "a"], ["a", "b", "c", "d"]) == ["c", "d", "a", "b"]
    assert fill_missing([], ["a", "b"]) == ["a", "b"]
    assert fill_missing(["b"], ["a", "b"]) == ["a", "b"]
    assert fill_missing(["a", "a"], ["a"]) == ["a"]
    assert fill_missing(["x"], ["a", "x", "b"], only={"b"}) == ["x", "b"]


# ── Navigation ─────────────────────────────────────────────────────────


async def test_navigation_requires_auth(client):
    assert (await client.get(NAV)).status_code == 401


async def test_existing_host_gets_default_navigation(client, auth_headers):
    body = (await client.get(NAV, headers=auth_headers)).json()
    assert _ids(body) == DEFAULT_NAV_IDS
    assert _visible(body) == DEFAULT_NAV_IDS
    assert body["is_default"] is True and body["revision"] == 0


async def test_reorder_and_hide_persist(client, auth_headers, db_session, test_user):
    order = list(reversed(MOVABLE))
    resp = await client.put(NAV, json={"order": order, "hidden": ["guests", "faq"], "expected_revision": 0}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert _ids(body) == ["overview", *order, "settings"]
    assert {i["id"] for i in body["items"] if i["hidden"]} == {"guests", "faq"}
    assert body["revision"] == 1 and body["is_default"] is False

    # Survives a fresh session/device: it's in Postgres, not the browser.
    again = (await client.get(NAV, headers=auth_headers)).json()
    assert _ids(again) == _ids(body)
    row = await db_session.scalar(select(UserUiPreference).where(UserUiPreference.user_id == test_user.id))
    assert row.key == "navigation" and row.revision == 1

    # Restore one hidden entry.
    resp = await client.put(NAV, json={"order": order, "hidden": ["faq"], "expected_revision": 1}, headers=auth_headers)
    assert {i["id"] for i in resp.json()["items"] if i["hidden"]} == {"faq"}


async def test_pinned_destinations_stay_fixed(client, auth_headers):
    resp = await client.put(
        NAV, json={"order": ["settings", *MOVABLE, "overview"], "hidden": [], "expected_revision": 0}, headers=auth_headers
    )
    assert resp.status_code == 200
    ids = _ids(resp.json())
    assert ids[0] == "overview" and ids[-1] == "settings"

    for essential in ("overview", "settings", "properties"):
        resp = await client.put(NAV, json={"order": MOVABLE, "hidden": [essential], "expected_revision": 1}, headers=auth_headers)
        assert resp.status_code == 422, essential


@pytest.mark.parametrize(
    "payload",
    [
        {"order": ["nope"], "hidden": []},
        {"order": MOVABLE, "hidden": ["nope"]},
        {"order": ["calls", "calls"], "hidden": []},
        {"order": MOVABLE, "hidden": ["faq", "faq"]},
    ],
)
async def test_invalid_navigation_rejected(client, auth_headers, payload):
    resp = await client.put(NAV, json={**payload, "expected_revision": 0}, headers=auth_headers)
    assert resp.status_code == 422
    assert (await client.get(NAV, headers=auth_headers)).json()["is_default"] is True


async def test_omitted_items_keep_their_position(client, auth_headers):
    await client.put(NAV, json={"order": list(reversed(MOVABLE)), "hidden": [], "expected_revision": 0}, headers=auth_headers)
    # Client only sends two items -- everything else stays where it was.
    resp = await client.put(NAV, json={"order": ["faq", "analytics"], "hidden": [], "expected_revision": 1}, headers=auth_headers)
    assert sorted(_ids(resp.json())) == sorted(DEFAULT_NAV_IDS)


async def test_stale_revision_is_a_conflict_not_an_overwrite(client, auth_headers):
    await client.put(NAV, json={"order": MOVABLE, "hidden": ["faq"], "expected_revision": 0}, headers=auth_headers)
    resp = await client.put(NAV, json={"order": MOVABLE, "hidden": [], "expected_revision": 0}, headers=auth_headers)
    assert resp.status_code == 409
    body = (await client.get(NAV, headers=auth_headers)).json()
    assert body["revision"] == 1
    assert {i["id"] for i in body["items"] if i["hidden"]} == {"faq"}


async def test_reset_navigation(client, auth_headers):
    await client.put(NAV, json={"order": list(reversed(MOVABLE)), "hidden": ["faq"], "expected_revision": 0}, headers=auth_headers)
    resp = await client.delete(NAV, headers=auth_headers)
    assert resp.status_code == 200
    assert _visible(resp.json()) == DEFAULT_NAV_IDS and resp.json()["is_default"] is True


async def test_navigation_is_per_account(client, auth_headers, db_session):
    other = await _other_user(db_session)
    await client.put(NAV, json={"order": list(reversed(MOVABLE)), "hidden": ["faq"], "expected_revision": 0}, headers=auth_headers)
    other_body = (await client.get(NAV, headers=auth_headers_for(other))).json()
    assert other_body["is_default"] is True and _visible(other_body) == DEFAULT_NAV_IDS
    # The other account's first save starts from its own revision 0.
    resp = await client.put(NAV, json={"order": MOVABLE, "hidden": [], "expected_revision": 0}, headers=auth_headers_for(other))
    assert resp.status_code == 200


async def test_hiding_a_page_never_changes_capabilities(client, auth_headers, db_session, test_user):
    await client.put(NAV, json={"order": MOVABLE, "hidden": ["faq", "guests", "calendar"], "expected_revision": 0}, headers=auth_headers)
    rows = (await db_session.scalars(select(HostCapability).where(HostCapability.user_id == test_user.id))).all()
    assert rows == []
    caps = {c["id"]: c for c in (await client.get("/api/v1/capabilities", headers=auth_headers)).json()["capabilities"]}
    assert caps["knowledge_faq"]["enabled"] and caps["guest_memory"]["enabled"] and caps["calendar_sync"]["enabled"]


# ── Capability reconciliation (synthetic optional-only destination) ───


@pytest.fixture
def optional_only_destination(monkeypatch):
    """No real destination depends solely on an optional capability today
    (every page has a core capability or an independent purpose), so
    exercise reconciliation with one that does."""
    dest = NavDestination("tech_page", "Technicians", "/dashboard/technicians", ("technician_dispatch",))
    dests = (*NAV_DESTINATIONS[:-1], dest, NAV_DESTINATIONS[-1])
    monkeypatch.setattr(ui_preferences_service, "NAV_DESTINATIONS", dests)
    monkeypatch.setattr(ui_preferences_service, "NAV_BY_ID", {d.id: d for d in dests})
    monkeypatch.setattr(ui_preferences_service, "_MOVABLE_DEFAULT", [d.id for d in dests if d.placement == "movable"])
    return dest


async def test_disabled_capability_hides_destination_and_reenable_restores_it(
    client, auth_headers, optional_only_destination
):
    movable = [*MOVABLE, "tech_page"]
    custom = ["tech_page", *reversed(MOVABLE)]
    await client.put(NAV, json={"order": custom, "hidden": [], "expected_revision": 0}, headers=auth_headers)

    await client.patch("/api/v1/capabilities/technician_dispatch", json={"enabled": False}, headers=auth_headers)
    body = (await client.get(NAV, headers=auth_headers)).json()
    item = next(i for i in body["items"] if i["id"] == "tech_page")
    assert item["available"] is False and "Technician dispatch" in item["unavailable_reason"]
    assert "tech_page" not in _visible(body)

    # Can't be added back while its capability is off...
    resp = await client.put(NAV, json={"order": movable, "hidden": [], "expected_revision": 1}, headers=auth_headers)
    assert resp.status_code == 422
    # ...and saving other changes meanwhile keeps its slot.
    resp = await client.put(NAV, json={"order": list(reversed(MOVABLE)), "hidden": [], "expected_revision": 1}, headers=auth_headers)
    assert resp.status_code == 200

    await client.patch("/api/v1/capabilities/technician_dispatch", json={"enabled": True}, headers=auth_headers)
    body = (await client.get(NAV, headers=auth_headers)).json()
    assert _visible(body)[1] == "tech_page"  # back at the top of the movable list, where the host put it


# ── Overview widgets ───────────────────────────────────────────────────


async def test_default_overview_layout_matches_original_overview(client, auth_headers):
    body = (await client.get(WIDGETS, headers=auth_headers)).json()
    shown = [w["id"] for w in body["widgets"] if not w["hidden"]]
    assert shown == [
        "portfolio_snapshot",
        "needs_attention",
        "live_requests",
        "opportunities",
        "recent_calls",
        "unanswered_questions",
    ]
    assert next(w for w in body["widgets"] if w["id"] == "busy_call_recovery")["hidden"] is True
    assert body["is_default"] is True


async def test_add_hide_reorder_resize_persist(client, auth_headers):
    entries = _widget_entries((await client.get(WIDGETS, headers=auth_headers)).json())
    by_id = {e["id"]: e for e in entries}
    by_id["busy_call_recovery"]["hidden"] = False  # add
    by_id["opportunities"]["hidden"] = True  # hide
    by_id["recent_calls"]["size"] = "full"  # resize
    order = ["recent_calls", "busy_call_recovery", "portfolio_snapshot", "needs_attention", "live_requests", "opportunities", "unanswered_questions"]
    resp = await client.put(WIDGETS, json={"widgets": [by_id[i] for i in order], "expected_revision": 0}, headers=auth_headers)
    assert resp.status_code == 200

    again = (await client.get(WIDGETS, headers=auth_headers)).json()
    assert [w["id"] for w in again["widgets"]] == order
    widgets = {w["id"]: w for w in again["widgets"]}
    assert widgets["recent_calls"]["size"] == "full"
    assert widgets["busy_call_recovery"]["hidden"] is False
    assert widgets["opportunities"]["hidden"] is True


@pytest.mark.parametrize(
    "bad",
    [
        {"id": "portfolio_snapshot", "size": "half", "hidden": False},  # unsupported size for this widget
        {"id": "made_up", "size": "half", "hidden": False},
    ],
)
async def test_invalid_widget_entries_rejected(client, auth_headers, bad):
    resp = await client.put(WIDGETS, json={"widgets": [bad], "expected_revision": 0}, headers=auth_headers)
    assert resp.status_code == 422


async def test_arbitrary_widget_size_rejected_by_schema(client, auth_headers):
    resp = await client.put(
        WIDGETS, json={"widgets": [{"id": "recent_calls", "size": "937px", "hidden": False}], "expected_revision": 0}, headers=auth_headers
    )
    assert resp.status_code == 422


async def test_duplicate_widgets_rejected(client, auth_headers):
    entry = {"id": "recent_calls", "size": "half", "hidden": False}
    resp = await client.put(WIDGETS, json={"widgets": [entry, entry], "expected_revision": 0}, headers=auth_headers)
    assert resp.status_code == 422


async def test_reset_widgets(client, auth_headers):
    entries = _widget_entries((await client.get(WIDGETS, headers=auth_headers)).json())
    for e in entries:
        e["hidden"] = True
    await client.put(WIDGETS, json={"widgets": entries, "expected_revision": 0}, headers=auth_headers)
    body = (await client.delete(WIDGETS, headers=auth_headers)).json()
    assert body["is_default"] is True
    assert sum(1 for w in body["widgets"] if not w["hidden"]) == 6


async def test_widget_conflict(client, auth_headers):
    entries = _widget_entries((await client.get(WIDGETS, headers=auth_headers)).json())
    assert (await client.put(WIDGETS, json={"widgets": entries, "expected_revision": 0}, headers=auth_headers)).status_code == 200
    assert (await client.put(WIDGETS, json={"widgets": entries, "expected_revision": 0}, headers=auth_headers)).status_code == 409


async def test_widgets_are_per_account(client, auth_headers, db_session):
    other = await _other_user(db_session)
    entries = _widget_entries((await client.get(WIDGETS, headers=auth_headers)).json())
    for e in entries:
        e["hidden"] = True
    await client.put(WIDGETS, json={"widgets": entries, "expected_revision": 0}, headers=auth_headers)
    assert (await client.get(WIDGETS, headers=auth_headers_for(other))).json()["is_default"] is True


async def test_stored_layout_with_removed_widget_and_bad_size_resolves_safely(client, auth_headers, db_session, test_user):
    db_session.add(
        UserUiPreference(
            user_id=test_user.id,
            key="overview_widgets",
            revision=3,
            data={"widgets": [{"id": "retired_widget", "size": "full"}, {"id": "portfolio_snapshot", "size": "half"}, "garbage"]},
        )
    )
    await db_session.commit()
    body = (await client.get(WIDGETS, headers=auth_headers)).json()
    ids = [w["id"] for w in body["widgets"]]
    assert "retired_widget" not in ids
    assert set(ids) == set(WIDGETS_BY_ID)
    assert next(w for w in body["widgets"] if w["id"] == "portfolio_snapshot")["size"] == "full"


@pytest.fixture
def optional_only_widget(monkeypatch):
    widget = OverviewWidget(
        "tech_widget", "Technicians", "", ("technician_dispatch",), default_visible=True, default_size="half", sizes=("half", "full")
    )
    widgets = (*OVERVIEW_WIDGETS, widget)
    monkeypatch.setattr(ui_preferences_service, "WIDGETS_BY_ID", {w.id: w for w in widgets})
    monkeypatch.setattr(ui_preferences_service, "_WIDGET_DEFAULT_ORDER", [w.id for w in widgets])
    return widget


async def test_widget_for_disabled_capability_is_kept_and_restored(client, auth_headers, optional_only_widget):
    entries = _widget_entries((await client.get(WIDGETS, headers=auth_headers)).json())
    tech = next(e for e in entries if e["id"] == "tech_widget")
    tech["size"] = "full"
    entries = [tech, *[e for e in entries if e["id"] != "tech_widget"]]
    await client.put(WIDGETS, json={"widgets": entries, "expected_revision": 0}, headers=auth_headers)

    await client.patch("/api/v1/capabilities/technician_dispatch", json={"enabled": False}, headers=auth_headers)
    body = (await client.get(WIDGETS, headers=auth_headers)).json()
    assert next(w for w in body["widgets"] if w["id"] == "tech_widget")["available"] is False

    # Not offerable while off...
    resp = await client.put(WIDGETS, json={"widgets": entries, "expected_revision": 1}, headers=auth_headers)
    assert resp.status_code == 422
    # ...other edits don't lose its settings.
    others = [e for e in _widget_entries(body) if e["id"] != "tech_widget"]
    assert (await client.put(WIDGETS, json={"widgets": others, "expected_revision": 1}, headers=auth_headers)).status_code == 200

    await client.patch("/api/v1/capabilities/technician_dispatch", json={"enabled": True}, headers=auth_headers)
    body = (await client.get(WIDGETS, headers=auth_headers)).json()
    restored = next(w for w in body["widgets"] if w["id"] == "tech_widget")
    assert restored["available"] is True and restored["size"] == "full"
    assert body["widgets"][0]["id"] == "tech_widget"


async def test_widget_visibility_never_touches_capabilities(client, auth_headers, db_session, test_user):
    entries = _widget_entries((await client.get(WIDGETS, headers=auth_headers)).json())
    for e in entries:
        e["hidden"] = True
    await client.put(WIDGETS, json={"widgets": entries, "expected_revision": 0}, headers=auth_headers)
    rows = (await db_session.scalars(select(HostCapability).where(HostCapability.user_id == test_user.id))).all()
    assert rows == []


async def test_save_failure_returns_clear_error(client, auth_headers, monkeypatch):
    async def failing_write(*args, **kwargs):
        raise ui_preferences_service.PreferencePersistenceError("Couldn't save your layout right now. Nothing was changed.")

    monkeypatch.setattr(ui_preferences_service, "_write", failing_write)
    resp = await client.put(NAV, json={"order": MOVABLE, "hidden": [], "expected_revision": 0}, headers=auth_headers)
    assert resp.status_code == 503
    assert "Nothing was changed" in resp.json()["detail"]


async def test_usable_capabilities_exclude_unavailable_integrations(db_session, test_user, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "bright_data_api_key", None)
    usable = await capability_service.usable_capability_ids(db_session, test_user)
    assert "airbnb_import" not in usable
    assert "guest_support_agent" in usable


# ── Analytics layout ────────────────────────────────────────────────────

from app.services.analytics_widget_registry import ANALYTICS_METRICS, DEFAULT_LAYOUT, METRICS_BY_ID  # noqa: E402

ANALYTICS = "/api/v1/preferences/analytics-widgets"


def _analytics_input(body: dict) -> list[dict]:
    """The layout as the customizer sends it back (available entries)."""
    out = []
    for e in body["entries"]:
        if e["type"] == "heading":
            out.append({"type": "heading", "id": e["id"], "title": e["title"], "subtitle": e["subtitle"]})
        elif e["available"]:
            out.append({"type": "metric", "id": e["id"], "size": e["size"], "hidden": e["hidden"]})
    return out


def test_every_analytics_metric_has_definition_and_frontend_renderer():
    source = (FRONTEND / "components" / "analytics" / "analytics-sections.tsx").read_text()
    for metric in ANALYTICS_METRICS:
        assert metric.description and metric.formula, metric.id
        assert metric.default_size in metric.sizes
        for cap in metric.capabilities:
            assert cap in capability_registry.CAPABILITIES_BY_ID, f"{metric.id} -> {cap}"
        has_tile = re.search(rf"^\s+{metric.id}: \(", source, re.M)
        has_panel = f'case "{metric.id}":' in source
        assert has_tile or has_panel, f"no frontend renderer for {metric.id}"
    assert {e["id"] for e in DEFAULT_LAYOUT if e["type"] == "metric"} == set(METRICS_BY_ID)


async def test_default_analytics_layout_reproduces_original_page(client, auth_headers):
    body = (await client.get(ANALYTICS, headers=auth_headers)).json()
    headings = [e["title"] for e in body["entries"] if e["type"] == "heading"]
    assert headings == ["Portfolio performance", "Mira impact", "Pricing & negotiation"]
    occupancy = next(e for e in body["entries"] if e["id"] == "occupancy")
    assert occupancy["formula"].startswith("Booked nights ÷ available nights")
    assert body["is_default"] is True


async def test_analytics_reorder_resize_hide_and_edit_headings(client, auth_headers):
    entries = _analytics_input((await client.get(ANALYTICS, headers=auth_headers)).json())
    by_id = {e["id"]: e for e in entries}
    by_id["revpar"]["size"] = "md"
    by_id["avg_discount"]["hidden"] = True
    by_id["portfolio"]["title"] = "How we're doing"
    by_id["portfolio"]["subtitle"] = None
    entries = [e for e in entries if e["id"] != "pricing"]  # delete a heading
    entries.insert(0, {"type": "heading", "id": "h-mine", "title": "Money first", "subtitle": "My view"})
    entries.remove(by_id["revenue"])
    entries.insert(1, by_id["revenue"])

    resp = await client.put(ANALYTICS, json={"entries": entries, "expected_revision": 0}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    saved = (await client.get(ANALYTICS, headers=auth_headers)).json()["entries"]
    ids = [e["id"] for e in saved]
    assert ids[:2] == ["h-mine", "revenue"]
    assert "pricing" not in ids  # a deleted heading stays deleted
    saved_by_id = {e["id"]: e for e in saved}
    assert saved_by_id["revpar"]["size"] == "md"
    assert saved_by_id["avg_discount"]["hidden"] is True
    assert saved_by_id["portfolio"]["title"] == "How we're doing"


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "metric", "id": "occupancy", "size": "full"},  # tiles are sm/md only
        {"type": "metric", "id": "booking_funnel", "size": "sm"},  # panels are half/full only
        {"type": "metric", "id": "made_up", "size": "sm"},
        {"type": "heading", "id": "h-x", "title": "   "},
        {"type": "heading", "id": "occupancy", "title": "Clash"},
        {"type": "heading", "id": "Bad Id!", "title": "x"},
    ],
)
async def test_invalid_analytics_entries_rejected(client, auth_headers, bad):
    resp = await client.put(ANALYTICS, json={"entries": [bad], "expected_revision": 0}, headers=auth_headers)
    assert resp.status_code == 422


async def test_heading_limit(client, auth_headers):
    headings = [{"type": "heading", "id": f"h-{i}", "title": f"H{i}"} for i in range(21)]
    resp = await client.put(ANALYTICS, json={"entries": headings, "expected_revision": 0}, headers=auth_headers)
    assert resp.status_code == 422


async def test_negotiation_off_hides_negotiation_metrics_and_restores_them(client, auth_headers):
    entries = _analytics_input((await client.get(ANALYTICS, headers=auth_headers)).json())
    neg = next(e for e in entries if e["id"] == "avg_discount")
    neg["size"] = "md"
    entries = [neg, *[e for e in entries if e["id"] != "avg_discount"]]
    await client.put(ANALYTICS, json={"entries": entries, "expected_revision": 0}, headers=auth_headers)

    await client.patch("/api/v1/capabilities/negotiation", json={"enabled": False}, headers=auth_headers)
    body = (await client.get(ANALYTICS, headers=auth_headers)).json()
    unavailable = {e["id"] for e in body["entries"] if e["type"] == "metric" and not e["available"]}
    assert unavailable == {"negotiated_price", "negotiation_conversion", "avg_discount"}
    # Saving other edits meanwhile keeps their place and size.
    resp = await client.put(ANALYTICS, json={"entries": _analytics_input(body), "expected_revision": 1}, headers=auth_headers)
    assert resp.status_code == 200

    await client.patch("/api/v1/capabilities/negotiation", json={"enabled": True}, headers=auth_headers)
    body = (await client.get(ANALYTICS, headers=auth_headers)).json()
    assert body["entries"][0]["id"] == "avg_discount" and body["entries"][0]["size"] == "md"


async def test_analytics_layout_conflict_and_reset(client, auth_headers):
    entries = _analytics_input((await client.get(ANALYTICS, headers=auth_headers)).json())
    assert (await client.put(ANALYTICS, json={"entries": entries, "expected_revision": 0}, headers=auth_headers)).status_code == 200
    assert (await client.put(ANALYTICS, json={"entries": entries, "expected_revision": 0}, headers=auth_headers)).status_code == 409
    body = (await client.delete(ANALYTICS, headers=auth_headers)).json()
    assert body["is_default"] is True
