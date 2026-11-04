"""Tickets: the durable follow-up behind everything Mira tells a guest the host will do."""

import uuid

import pytest
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.integrations import twilio_client
from app.models.notification import Notification
from app.models.technician import Technician
from app.models.ticket import Ticket
from app.models.user import User
from app.prompts.system_prompt import build_lead_system_prompt, build_system_prompt
from app.schemas.tool import (
    DispatchTechnicianArgs,
    EscalateToHostArgs,
    RequestHostTransferArgs,
    SendPhotosArgs,
    SendWhatsappArgs,
)
from app.services import call_summary_service, ticket_service, tool_handlers
from tests.conftest import auth_headers_for


@pytest.fixture(autouse=True)
def _no_outbound_messages(monkeypatch):
    """No email or WhatsApp leaves the test run."""

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(tool_handlers, "_send_escalation_email", _noop)
    monkeypatch.setattr(tool_handlers, "_send_escalation_whatsapp", _noop)
    monkeypatch.setattr(twilio_client, "send_whatsapp_best_effort", _noop)


async def _tickets(call_session_id):
    async with AsyncSessionLocal() as db:
        return list((await db.scalars(select(Ticket).where(Ticket.call_session_id == call_session_id))).all())


# ---- the service ------------------------------------------------------


async def test_the_same_promise_in_one_call_is_one_ticket(db_session, test_user, test_call_session):
    first = await ticket_service.open_ticket(
        db_session, kind="escalation", summary="Finalise booking", user_id=test_user.id,
        call_session_id=test_call_session.id,
    )
    again = await ticket_service.open_ticket(
        db_session, kind="escalation", summary="Finalise booking", user_id=test_user.id,
        call_session_id=test_call_session.id,
    )
    assert first is not None and again is not None and first.id == again.id
    assert len(await _tickets(test_call_session.id)) == 1


async def test_opening_a_ticket_never_raises(db_session):
    # No host can be found -> skipped quietly, not an exception on a live call.
    assert await ticket_service.open_ticket(db_session, kind="escalation", summary="x") is None
    # A blank summary is not a ticket.
    assert await ticket_service.open_ticket(db_session, kind="escalation", summary="  ", user_id=uuid.uuid4()) is None
    # A host that does not exist violates the foreign key; the failure is contained.
    assert await ticket_service.open_ticket(db_session, kind="escalation", summary="x", user_id=uuid.uuid4()) is None


async def test_host_is_resolved_from_the_property_or_the_call(db_session, test_user, test_property, test_call_session):
    by_property = await ticket_service.open_ticket(
        db_session, kind="photos", summary="From a property", property_id=test_property.id
    )
    by_call = await ticket_service.open_ticket(
        db_session, kind="photos", summary="From a call", call_session_id=test_call_session.id
    )
    assert by_property.user_id == test_user.id and by_call.user_id == test_user.id


async def test_an_unknown_urgency_falls_back_to_medium(db_session, test_user):
    ticket = await ticket_service.open_ticket(
        db_session, kind="escalation", summary="Odd urgency", urgency="asap!!", user_id=test_user.id
    )
    assert ticket.urgency == "medium"


# ---- the API ----------------------------------------------------------


async def test_api_lists_open_tickets_and_the_host_can_resolve_them(
    client, auth_headers, test_user, test_property, test_call_session, db_session
):
    ticket = await ticket_service.open_ticket(
        db_session, kind="escalation", summary="Finalise booking", urgency="high", user_id=test_user.id,
        property_id=test_property.id, call_session_id=test_call_session.id,
    )
    listed = (await client.get("/api/v1/tickets", headers=auth_headers)).json()
    assert [t["id"] for t in listed] == [str(ticket.id)]
    assert listed[0]["property_name"] == test_property.name and listed[0]["guest_phone"] == "+919999999999"

    done = await client.patch(f"/api/v1/tickets/{ticket.id}", headers=auth_headers, json={"status": "done"})
    assert done.status_code == 200 and done.json()["status"] == "done" and done.json()["resolved_at"]

    assert (await client.get("/api/v1/tickets", headers=auth_headers)).json() == []
    resolved = (await client.get("/api/v1/tickets?state=resolved", headers=auth_headers)).json()
    assert [t["id"] for t in resolved] == [str(ticket.id)]

    reopened = await client.patch(f"/api/v1/tickets/{ticket.id}", headers=auth_headers, json={"status": "open"})
    assert reopened.json()["status"] == "open" and reopened.json()["resolved_at"] is None


async def test_api_never_shows_or_changes_another_hosts_tickets(client, auth_headers, test_user, db_session):
    stranger = User(email=f"s-{uuid.uuid4().hex[:6]}@example.com", clerk_user_id=f"user_{uuid.uuid4().hex[:12]}")
    db_session.add(stranger)
    await db_session.commit()
    theirs = await ticket_service.open_ticket(db_session, kind="escalation", summary="Not yours", user_id=stranger.id)

    assert (await client.get("/api/v1/tickets?state=all", headers=auth_headers)).json() == []
    response = await client.patch(f"/api/v1/tickets/{theirs.id}", headers=auth_headers, json={"status": "done"})
    assert response.status_code == 404
    assert (await client.patch(f"/api/v1/tickets/{theirs.id}", headers=auth_headers, json={"status": "bogus"})).status_code == 422
    assert (await client.get("/api/v1/tickets", headers=auth_headers_for(stranger))).json()[0]["id"] == str(theirs.id)


# ---- tickets opened by the tools --------------------------------------


async def test_escalating_to_the_host_opens_a_ticket_linked_to_its_alert(
    db_session, test_user, test_property, test_call_session
):
    args = EscalateToHostArgs(
        property_id=str(test_property.id), reason="Finalise booking", urgency="high", guest_phone="+919999999999"
    )
    await tool_handlers.handle_escalate_to_host(db_session, args, test_call_session.id, test_user.id)

    (ticket,) = await _tickets(test_call_session.id)
    assert ticket.kind == "escalation" and ticket.summary == "Finalise booking" and ticket.urgency == "high"
    assert ticket.property_id == test_property.id and ticket.status == "open" and ticket.source == "tool"
    async with AsyncSessionLocal() as db:
        alert = await db.get(Notification, ticket.notification_id)
    assert alert.channel == "escalation"


async def test_a_transfer_the_host_may_have_missed_leaves_a_ticket(db_session, test_user, test_property, test_call_session):
    await tool_handlers._record_transfer_for_follow_up(
        db_session,
        RequestHostTransferArgs(reason="Wants to haggle"),
        test_call_session.id,
        test_property.id,
        test_user.id,
        None,
    )
    (ticket,) = await _tickets(test_call_session.id)
    assert ticket.kind == "transfer" and ticket.urgency == "high" and "Wants to haggle" in ticket.summary


async def test_technician_requests_leave_a_ticket_in_every_case(db_session, test_user, test_property, test_call_session):
    args = DispatchTechnicianArgs(property_id=str(test_property.id), issue_type="plumbing", urgency="high")

    await tool_handlers.handle_dispatch_technician(db_session, args, test_call_session.id)  # none on file
    (no_tech,) = await _tickets(test_call_session.id)
    assert no_tech.kind == "technician" and "No plumbing technician" in no_tech.detail

    async with AsyncSessionLocal() as db:
        db.add(Technician(property_id=test_property.id, name="Ravi", phone="+911111111111", specialty="plumbing", rating=4.5))
        await db.commit()
    await tool_handlers.handle_dispatch_technician(db_session, args, test_call_session.id)
    kinds = sorted(t.summary for t in await _tickets(test_call_session.id))
    assert len(kinds) == 2 and any("Ravi" in k for k in kinds)


async def test_asking_for_photos_when_none_exist_is_recorded_not_just_said(
    db_session, test_user, test_property, test_call_session
):
    assert not test_property.photos
    reply = await tool_handlers.handle_send_photos(
        db_session, SendPhotosArgs(property_id=str(test_property.id), guest_phone="+919876543210"),
        test_call_session.id, test_user.id,
    )
    assert "flag that to the host" in reply  # the promise the guest hears ...
    (ticket,) = await _tickets(test_call_session.id)  # ... now has something behind it
    assert ticket.kind == "photos" and "9876543210" in ticket.summary
    async with AsyncSessionLocal() as db:
        assert (await db.get(Notification, ticket.notification_id)) is not None


async def test_photos_that_were_sent_leave_no_ticket_but_unsent_ones_do(
    db_session, test_user, test_property, test_call_session, monkeypatch
):
    async with AsyncSessionLocal() as db:
        prop = await db.get(type(test_property), test_property.id)
        prop.photos = ["https://example.com/a.jpg"]
        await db.commit()
    args = SendPhotosArgs(property_id=str(test_property.id), guest_phone="+919876543210")

    monkeypatch.setattr(settings, "twilio_enabled", True)
    async with AsyncSessionLocal() as db:  # a fresh session sees the photos just saved
        await tool_handlers.handle_send_photos(db, args, test_call_session.id, test_user.id)
    assert await _tickets(test_call_session.id) == []  # delivered: nothing is owed

    monkeypatch.setattr(settings, "twilio_enabled", False)
    async with AsyncSessionLocal() as db:
        await tool_handlers.handle_send_photos(db, args, test_call_session.id, test_user.id)
    (ticket,) = await _tickets(test_call_session.id)
    assert ticket.kind == "photos" and "could not send" in ticket.detail


async def test_a_whatsapp_that_could_not_be_sent_leaves_a_ticket(
    db_session, test_property, test_call_session, monkeypatch
):
    args = SendWhatsappArgs(phone="+919876543210", message="Your check-in is at 2pm")
    monkeypatch.setattr(settings, "twilio_enabled", True)
    await tool_handlers.handle_send_whatsapp(db_session, args, test_property.id, test_call_session.id)
    assert await _tickets(test_call_session.id) == []

    monkeypatch.setattr(settings, "twilio_enabled", False)
    await tool_handlers.handle_send_whatsapp(db_session, args, test_property.id, test_call_session.id)
    (ticket,) = await _tickets(test_call_session.id)
    assert ticket.kind == "whatsapp" and "Your check-in is at 2pm" in ticket.detail


# ---- the end-of-call backstop -----------------------------------------


async def test_a_promise_nothing_recorded_gets_a_ticket_and_an_alert(db_session, test_user, test_call_session):
    await ticket_service.reconcile_promises(
        db_session,
        host_user_id=test_user.id,
        call_session_id=test_call_session.id,
        promises=["The host will call you back tomorrow morning", "I will email the brochure"],
    )
    (ticket,) = await _tickets(test_call_session.id)
    assert ticket.kind == "promise" and ticket.source == "post_call"
    assert ticket.summary == "The host will call you back tomorrow morning (+1 more)"
    assert "brochure" in ticket.detail
    async with AsyncSessionLocal() as db:
        alert = await db.get(Notification, ticket.notification_id)
    assert alert.channel == "system" and "Tickets" in alert.message


async def test_promises_are_added_to_an_existing_ticket_once(db_session, test_user, test_property, test_call_session):
    await tool_handlers.handle_escalate_to_host(
        db_session,
        EscalateToHostArgs(property_id=str(test_property.id), reason="Finalise booking", urgency="medium"),
        test_call_session.id,
        test_user.id,
    )
    for _ in range(2):  # running the check twice must not duplicate anything
        await ticket_service.reconcile_promises(
            db_session, host_user_id=test_user.id, call_session_id=test_call_session.id,
            promises=["The host will send the payment details"],
        )
    (ticket,) = await _tickets(test_call_session.id)  # still just the escalation
    assert ticket.kind == "escalation" and ticket.detail.count("Also promised on the call:") == 1
    assert "payment details" in ticket.detail


async def test_no_promises_means_no_ticket(db_session, test_user, test_call_session):
    await ticket_service.reconcile_promises(
        db_session, host_user_id=test_user.id, call_session_id=test_call_session.id, promises=[]
    )
    await ticket_service.reconcile_promises(
        db_session, host_user_id=test_user.id, call_session_id=test_call_session.id, promises=["  ", ""]
    )
    assert await _tickets(test_call_session.id) == []


# ---- the summary and the prompt ---------------------------------------


def test_summary_parses_promises_and_survives_a_model_that_omits_them():
    with_promises = call_summary_service._parse_summary_response(
        '{"conversation_summary": "x", "promises_made": ["Host will call back", "  ", 7]}'
    )
    assert with_promises.promises_made == ["Host will call back", "7"]
    without = call_summary_service._parse_summary_response('{"conversation_summary": "x"}')
    assert without.promises_made == []
    assert "promises_made" in call_summary_service._SUMMARY_PROMPT


def test_both_agents_are_told_not_to_promise_what_nothing_records(test_user, test_property):
    for prompt in (build_system_prompt(test_property, None, test_user), build_lead_system_prompt(test_user, [test_property])):
        assert "unless you have just made the tool call" in prompt


async def test_a_failed_ticket_does_not_disturb_the_callers_session(db_session, test_user, test_property):
    """open_ticket runs in the middle of a handler that keeps using the objects
    it already loaded. A ticket that cannot be saved must not expire them."""
    host_email = test_user.email  # loaded and in use by the caller
    assert await ticket_service.open_ticket(
        db_session, kind="escalation", summary="Will fail", user_id=uuid.uuid4()
    ) is None
    # Reading already-loaded state afterwards must not need the database again.
    assert test_user.email == host_email
    assert test_property.name


async def test_escalation_still_completes_when_its_ticket_cannot_be_saved(
    db_session, test_user, test_property, test_call_session, monkeypatch
):
    real = ticket_service.open_ticket

    async def failing(db, **kwargs):
        return await real(db, **{**kwargs, "user_id": uuid.uuid4()})  # a host that does not exist

    monkeypatch.setattr(ticket_service, "open_ticket", failing)
    args = EscalateToHostArgs(property_id=str(test_property.id), reason="Finalise booking", urgency="high")
    reply = await tool_handlers.handle_escalate_to_host(db_session, args, test_call_session.id, test_user.id)
    assert "flagged this to the host" in reply  # the guest still gets their answer
    async with AsyncSessionLocal() as db:
        alerts = list((await db.scalars(select(Notification).where(Notification.call_session_id == test_call_session.id))).all())
    assert len(alerts) == 1  # and the host still gets the alert


async def _settle():
    """Lets the detached background sends finish."""
    import asyncio

    for _ in range(5):
        await asyncio.sleep(0.02)


async def test_a_send_whatsapp_refuses_after_the_agent_said_sent_opens_a_ticket(
    db_session, test_property, test_call_session, monkeypatch
):
    monkeypatch.setattr(settings, "twilio_enabled", True)
    args = SendWhatsappArgs(phone="+919876543210", message="Your check-in is at 2pm")

    async def accepted(*a, **k):
        return True

    monkeypatch.setattr(twilio_client, "send_whatsapp_best_effort", accepted)
    await tool_handlers.handle_send_whatsapp(db_session, args, test_property.id, test_call_session.id)
    await _settle()
    assert await _tickets(test_call_session.id) == []  # accepted: nothing is owed

    async def refused(*a, **k):
        return False

    monkeypatch.setattr(twilio_client, "send_whatsapp_best_effort", refused)
    await tool_handlers.handle_send_whatsapp(db_session, args, test_property.id, test_call_session.id)
    await _settle()
    (ticket,) = await _tickets(test_call_session.id)
    assert ticket.kind == "whatsapp" and "did not accept" in ticket.detail


async def test_photos_refused_by_whatsapp_after_the_agent_said_sent_open_a_ticket(
    db_session, test_user, test_property, test_call_session, monkeypatch
):
    async with AsyncSessionLocal() as db:
        prop = await db.get(type(test_property), test_property.id)
        prop.photos = ["https://example.com/a.jpg"]
        await db.commit()
    monkeypatch.setattr(settings, "twilio_enabled", True)

    async def refused(*a, **k):
        return False

    monkeypatch.setattr(twilio_client, "send_whatsapp_best_effort", refused)
    async with AsyncSessionLocal() as db:
        reply = await tool_handlers.handle_send_photos(
            db, SendPhotosArgs(property_id=str(test_property.id), guest_phone="+919876543210"),
            test_call_session.id, test_user.id,
        )
    await _settle()
    assert "I've sent a photo gallery link" in reply
    (ticket,) = await _tickets(test_call_session.id)
    assert ticket.kind == "photos" and "did not accept" in ticket.detail


async def test_best_effort_reports_whether_twilio_accepted_the_message(monkeypatch):
    """The shared sender keeps its never-raises contract and now says how it went."""
    import app.integrations.twilio_client as real

    monkeypatch.undo()  # restore the real sender the autouse fixture replaced

    async def skipped(*a, **k):
        return {"status": "skipped", "reason": "not configured"}

    async def sent(*a, **k):
        return {"status": "queued"}

    async def broken(*a, **k):
        raise real.TwilioError("boom")

    monkeypatch.setattr(real, "send_whatsapp_message", skipped)
    assert await real.send_whatsapp_best_effort("+919999999999", "hi") is False
    monkeypatch.setattr(real, "send_whatsapp_message", sent)
    assert await real.send_whatsapp_best_effort("+919999999999", "hi") is True
    monkeypatch.setattr(real, "send_whatsapp_message", broken)
    assert await real.send_whatsapp_best_effort("+919999999999", "hi") is False
