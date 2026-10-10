import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.config import settings
from app.integrations import twilio_client
from app.models.call_session import CallSession
from app.models.faq_entry import FaqEntry
from app.models.notification import Notification
from app.models.property import Property
from app.models.unanswered_question import UnansweredQuestion
from app.models.user import User
from app.prompts.system_prompt import _persona_and_escalation_sections
from app.schemas.notification_preferences import NotificationPreferences
from app.schemas.tool import EscalateToHostArgs, RequestHostTransferArgs
from app.services import (
    call_summary_email,
    embedding_service,
    guest_calling_notification,
    notification_preferences_service as prefs_service,
    recovery_service,
    tool_handlers,
)
from tests.conftest import auth_headers_for
from tests.test_exotel_connect_routing import _call_session_for, _numbers, _property_with

SETTINGS = "/api/v1/notification-settings"


async def _set_prefs(db_session, user: User, **prefs) -> None:
    user.notification_preferences = NotificationPreferences(**prefs).model_dump(mode="json")
    await db_session.commit()
    await db_session.refresh(user)


async def _drain():
    # Let fire-and-forget create_task sends run.
    for _ in range(3):
        await asyncio.sleep(0)


# ── Defaults, API, validation ──────────────────────────────────────────


async def test_defaults_equal_pre_settings_behaviour(client, auth_headers):
    body = (await client.get(SETTINGS, headers=auth_headers)).json()
    p = body["preferences"]
    assert p["transfer_number_mode"] == "single"
    assert p["call_summary_email"] and p["escalation_email"]
    assert p["busy_call_alert"] and p["guest_calling_alert"] and p["guest_reply_alert"]
    assert p["stay_request_handling"] == "whatsapp" and p["connect_request_handling"] == "live_transfer"
    assert p["lead_labels"] == {"hot": "Hot", "warm": "Warm", "cold": "Cold", "not_qualified": "Not qualified"}
    assert "lead_label" in body["placeholders"]
    # Also on /auth/me, so the dashboard reads labels without another call.
    me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
    assert me["notification_preferences"]["lead_labels"]["hot"] == "Hot"


async def test_partial_update_persists(client, auth_headers, db_session, test_user):
    resp = await client.patch(
        SETTINGS,
        json={"busy_call_alert": False, "lead_labels": {"hot": "On fire", "warm": "Keen", "cold": "Browsing", "not_qualified": "New"}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    p = resp.json()["preferences"]
    assert p["busy_call_alert"] is False and p["call_summary_email"] is True
    await db_session.refresh(test_user)
    assert prefs_service.get(test_user).lead_labels.hot == "On fire"


@pytest.mark.parametrize(
    "payload",
    [
        {"call_summary_subject": "Hi {guest_name.__class__}"},
        {"call_summary_body": "Call from {nope}"},
        {"lead_labels": {"hot": "", "warm": "W", "cold": "C", "not_qualified": "N"}},
        {"lead_labels": {"hot": "Same", "warm": "same", "cold": "C", "not_qualified": "N"}},
        {"transfer_number_mode": "everywhere"},
        {"made_up_field": True},
    ],
)
async def test_invalid_updates_rejected(client, auth_headers, payload):
    assert (await client.patch(SETTINGS, json=payload, headers=auth_headers)).status_code == 422


async def test_malformed_stored_document_falls_back_to_defaults(db_session, test_user):
    test_user.notification_preferences = {"transfer_number_mode": 42}
    await db_session.commit()
    assert prefs_service.get(test_user) == NotificationPreferences()


async def test_preview_renders_draft_template_escaped(client, auth_headers):
    resp = await client.post(
        f"{SETTINGS}/preview",
        json={"subject": "{lead_label} lead: {property_name}", "body": "<b>{guest_name}</b> called about {property_name}"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["subject"] == "Hot lead: Sea View Villa"
    assert "&lt;b&gt;Priya Sharma&lt;/b&gt;" in body["html"]
    assert "<b>Priya" not in body["html"]


def test_lead_buckets_and_labels():
    prefs = NotificationPreferences()
    assert prefs_service.lead_bucket("very_hot") == "hot"
    assert prefs_service.lead_bucket(None) == "not_qualified"
    assert prefs_service.lead_label(prefs, "warm") == "Warm"
    assert prefs_service.lead_label(prefs, None) == "Not qualified"


# ── Transfer number resolution + per-property groups ────────────────


async def test_host_transfer_phone_modes(db_session, test_user, test_property):
    test_user.phone = "+919800000001"
    test_property.host_transfer_phone = "+919800000002"
    await db_session.commit()
    assert prefs_service.host_transfer_phone(test_user, test_property) == "+919800000001"  # single (default)
    await _set_prefs(db_session, test_user, transfer_number_mode="per_property")
    assert prefs_service.host_transfer_phone(test_user, test_property) == "+919800000002"
    assert prefs_service.host_transfer_phone(test_user, None) == "+919800000001"
    test_property.host_transfer_phone = None
    assert prefs_service.host_transfer_phone(test_user, test_property) == "+919800000001"


async def test_transfer_numbers_endpoint(client, auth_headers, db_session, test_user):
    a = await _property_with(db_session, test_user, name="A")
    b = await _property_with(db_session, test_user, name="B")
    c = await _property_with(db_session, test_user, name="C")
    resp = await client.put(
        "/api/v1/properties/transfer-numbers",
        json={"groups": [{"phone": "+919811111111", "property_ids": [str(a.id), str(b.id)]}]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    phones = {p["name"]: p["host_transfer_phone"] for p in resp.json()}
    assert phones == {"A": "+919811111111", "B": "+919811111111", "C": None}

    # A property in two groups, a short number, someone else's property.
    dup = {"groups": [{"phone": "+919811111111", "property_ids": [str(a.id)]}, {"phone": "+919822222222", "property_ids": [str(a.id)]}]}
    assert (await client.put("/api/v1/properties/transfer-numbers", json=dup, headers=auth_headers)).status_code == 422
    short = {"groups": [{"phone": "123", "property_ids": [str(a.id)]}]}
    assert (await client.put("/api/v1/properties/transfer-numbers", json=short, headers=auth_headers)).status_code == 422
    other = User(email=f"o-{uuid.uuid4().hex[:6]}@example.com")
    db_session.add(other)
    await db_session.commit()
    foreign = {"groups": [{"phone": "+919833333333", "property_ids": [str(a.id)]}]}
    assert (await client.put("/api/v1/properties/transfer-numbers", json=foreign, headers=auth_headers_for(other))).status_code == 404

    # Replacing with no groups clears every number.
    resp = await client.put("/api/v1/properties/transfer-numbers", json={"groups": []}, headers=auth_headers)
    assert all(p["host_transfer_phone"] is None for p in resp.json())
    await db_session.refresh(c)


async def test_transfer_requirement_is_mode_aware(client, auth_headers, db_session, test_user, test_property):
    def host_phone_met(body):
        cap = next(c for c in body["capabilities"] if c["id"] == "host_handoff")
        return next(r for r in cap["requirements"] if r["id"] == "host_phone")["met"]

    assert host_phone_met((await client.get("/api/v1/capabilities", headers=auth_headers)).json()) is False
    await _set_prefs(db_session, test_user, transfer_number_mode="per_property")
    test_property.host_transfer_phone = "+919844444444"
    await db_session.commit()
    assert host_phone_met((await client.get("/api/v1/capabilities", headers=auth_headers)).json()) is True


# ── Live-call paths ────────────────────────────────────────────────────


@pytest.fixture
def captured_sends(monkeypatch):
    sent = {"email": [], "whatsapp": []}

    async def fake_email(to_email, subject, body, html_body):
        sent["email"].append({"to": to_email, "subject": subject})

    async def fake_whatsapp(to_phone, **kwargs):
        sent["whatsapp"].append(to_phone)

    monkeypatch.setattr(tool_handlers, "_send_escalation_email", fake_email)
    monkeypatch.setattr(tool_handlers, "_send_escalation_whatsapp", fake_whatsapp)
    monkeypatch.setattr(settings, "twilio_enabled", True)
    return sent


async def test_escalation_uses_labels_group_number_and_email_toggle(db_session, test_user, test_property, captured_sends):
    test_user.phone = "+919800000001"
    test_property.host_transfer_phone = "+919800000002"
    await db_session.commit()
    await _set_prefs(db_session, test_user, transfer_number_mode="per_property",
                     lead_labels={"hot": "On fire", "warm": "Keen", "cold": "Browsing", "not_qualified": "New"})

    args = EscalateToHostArgs(property_id=str(test_property.id), reason="AC broken", urgency="high")
    await tool_handlers.handle_escalate_to_host(db_session, args, None, test_user.id)
    await _drain()
    assert captured_sends["whatsapp"] == ["+919800000002"]
    assert captured_sends["email"][0]["subject"].startswith("[New] High escalation")

    await _set_prefs(db_session, test_user, escalation_email=False)
    captured_sends["email"].clear()
    await tool_handlers.handle_escalate_to_host(db_session, args, None, test_user.id)
    await _drain()
    assert captured_sends["email"] == []
    # The in-app notification is never optional.
    notes = (await db_session.scalars(select(Notification).where(Notification.channel == "escalation"))).all()
    assert len(notes) == 2


async def test_no_live_transfers_when_host_prefers_whatsapp(db_session, test_user, test_property, captured_sends):
    test_user.phone = "+919800000001"
    await db_session.commit()
    await _set_prefs(db_session, test_user, connect_request_handling="whatsapp")
    session = await _call_session_for(db_session, test_property)

    reply = await tool_handlers.handle_request_host_transfer(
        db_session, RequestHostTransferArgs(reason="wants host"), session.id, test_property.id, test_user.id
    )
    await _drain()
    assert "WhatsApp" in reply
    await db_session.refresh(session)
    assert session.handoff_status is None  # no transfer claimed
    assert captured_sends["whatsapp"] == ["+919800000001"]


async def test_transfer_allowed_when_stay_requests_use_live_transfer(db_session, test_user, test_property, captured_sends):
    test_user.phone = "+919800000001"
    await db_session.commit()
    await _set_prefs(db_session, test_user, connect_request_handling="whatsapp", stay_request_handling="live_transfer")
    session = await _call_session_for(db_session, test_property)
    await tool_handlers.handle_request_host_transfer(
        db_session, RequestHostTransferArgs(reason="AC broken"), session.id, test_property.id, test_user.id
    )
    result = await db_session.execute(select(CallSession).where(CallSession.id == session.id))
    assert result.scalar_one().handoff_status == "requested"


async def test_lead_agent_transfer_dials_the_discussed_propertys_group_number(client, db_session, test_user, captured_sends):
    test_user.phone = "+919800000001"
    await db_session.commit()
    await _set_prefs(db_session, test_user, transfer_number_mode="per_property")
    property_ = await _property_with(db_session, test_user, host_transfer_phone="+919800000003")
    # Lead Agent call: no property on the session; the guest settled on one.
    session = await _call_session_for(db_session, property_, property_id=None)

    await tool_handlers.handle_request_host_transfer(
        db_session,
        RequestHostTransferArgs(reason="wants host"),
        session.id,
        None,
        test_user.id,
        context_property_id=property_.id,
    )
    resp = await client.get(
        "/api/v1/webhooks/exotel/connect-routing", params={"token": "test-token", "CallSid": session.exotel_call_id}
    )
    assert _numbers(resp) == ["+919800000003"]


async def test_guest_calling_alert_toggle(db_session, test_user, test_property, monkeypatch):
    test_user.phone = "+919800000001"
    await db_session.commit()
    await _set_prefs(db_session, test_user, guest_calling_alert=False)
    sent = []

    async def capture(to_phone, body):
        sent.append(to_phone)

    monkeypatch.setattr(twilio_client, "send_whatsapp_best_effort", capture)
    monkeypatch.setattr(settings, "twilio_guest_calling_template_sid", None)
    session = await _call_session_for(db_session, test_property)
    await guest_calling_notification.maybe_notify_guest_calling(test_property.id, session.id, "+919999999999")
    assert sent == []
    notes = (await db_session.scalars(select(Notification).where(Notification.call_session_id == session.id))).all()
    assert len(notes) == 1  # in-app notification still created

    # Positive control on a fresh call.
    await _set_prefs(db_session, test_user, guest_calling_alert=True)
    other = await _call_session_for(db_session, test_property)
    await guest_calling_notification.maybe_notify_guest_calling(test_property.id, other.id, "+919999999999")
    assert sent == ["+919800000001"]


async def test_busy_call_alert_toggle(db_session, test_user, test_property, monkeypatch):
    test_user.phone = "+919800000001"
    await db_session.commit()
    await _set_prefs(db_session, test_user, busy_call_alert=False)
    host_alerts = []

    async def capture(*args, **kwargs):
        host_alerts.append(args)

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(recovery_service, "_send_host_recovery_whatsapp", capture)
    monkeypatch.setattr(twilio_client, "send_whatsapp_best_effort", noop)
    monkeypatch.setattr(twilio_client, "send_whatsapp_template_best_effort", noop)
    await recovery_service._handle_busy_recovery(db_session, test_user.id, test_property.id, "+919777777777", test_property.exophone)
    await _drain()
    assert host_alerts == []

    # Positive control: with the alert on, the same call does alert the host.
    await _set_prefs(db_session, test_user, busy_call_alert=True)
    await recovery_service._handle_busy_recovery(db_session, test_user.id, test_property.id, "+919766666666", test_property.exophone)
    await _drain()
    assert host_alerts and host_alerts[0][0] == "+919800000001"


# ── Call summary email ───────────────────────────────────────────────


async def test_call_summary_email_toggle_and_template(db_session, test_user, test_property, monkeypatch):
    sent = []

    async def fake_send(to, subject, body, html_body=None):
        sent.append({"subject": subject, "body": body, "html": html_body})
        return {"status": "sent"}

    monkeypatch.setattr(call_summary_email.email_client, "send_email", fake_send)
    session = await _call_session_for(db_session, test_property)
    await _set_prefs(db_session, test_user, call_summary_subject="Summary for {property_name} ({lead_label})",
                     call_summary_body="Guest {guest_phone} -- {call_summary}")
    await call_summary_email.send_call_summary_email(db_session, session.id)
    assert sent[0]["subject"] == f"Summary for {test_property.name} (Not qualified)"
    assert sent[0]["body"].startswith("Guest +919999999999")

    await _set_prefs(db_session, test_user, call_summary_email=False)
    await call_summary_email.send_call_summary_email(db_session, session.id)
    assert len(sent) == 1


# ── Prompt lines ─────────────────────────────────────────────────────


async def test_prompt_unchanged_by_default_and_follows_preferences(db_session, test_user):
    default_sections = _persona_and_escalation_sections(test_user)
    await _set_prefs(db_session, test_user, connect_request_handling="whatsapp", stay_request_handling="live_transfer")
    sections = "".join(_persona_and_escalation_sections(test_user))
    assert "WhatsApp, not live transfers" in sections
    assert "call request_host_transfer instead of escalate_to_host" in sections
    await _set_prefs(db_session, test_user)
    assert _persona_and_escalation_sections(test_user) == default_sections


# ── FAQ editing ──────────────────────────────────────────────────────


async def test_editing_faq_question_refreshes_embedding(client, auth_headers, db_session, test_user, monkeypatch):
    backfills = []

    async def fake_backfill(entry_id, question):
        backfills.append(question)

    monkeypatch.setattr(embedding_service, "backfill_faq_entry_embedding", fake_backfill)
    entry = FaqEntry(user_id=test_user.id, question="Is wifi free?", answer="Yes", status="verified",
                     question_embedding=[0.1, 0.2])
    db_session.add(entry)
    await db_session.commit()

    resp = await client.patch(f"/api/v1/faq/{entry.id}", json={"answer": "Yes, 100 Mbps"}, headers=auth_headers)
    assert resp.status_code == 200 and resp.json()["status"] == "verified"
    await _drain()
    assert backfills == []  # answer-only edit keeps the embedding

    resp = await client.patch(f"/api/v1/faq/{entry.id}", json={"question": "How fast is the wifi?"}, headers=auth_headers)
    assert resp.json()["question"] == "How fast is the wifi?"
    await _drain()
    assert backfills == ["How fast is the wifi?"]
    await db_session.refresh(entry)
    assert entry.question_embedding is None


async def test_answering_a_gap_can_reword_the_question(client, auth_headers, db_session, test_user):
    gap = UnansweredQuestion(user_id=test_user.id, question="wifi pass?", normalized_question="wifi pass", status="pending")
    db_session.add(gap)
    await db_session.commit()
    resp = await client.post(
        f"/api/v1/faq/gaps/{gap.id}/answer",
        json={"answer": "It's on the fridge.", "question": "What is the Wi-Fi password?"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["question"] == "What is the Wi-Fi password?"
