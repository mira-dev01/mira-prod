"""Phase 5: guest_calling_notification.py -- the "guest is calling Mira
right now" host notification. maybe_notify_guest_calling opens its own DB
session (same shape as recovery_service.handle_busy_recovery), so these
tests call it directly rather than through the pipeline -- exercising the
exact same code path a real asyncio.create_task from pipeline.py would."""

import uuid

from app.integrations import twilio_client
from app.models.call_session import CallSession
from app.models.property import Property
from app.services import guest_calling_notification
from app.services.notification_service import list_notifications


async def _call_session_for(db_session, property_, **overrides) -> CallSession:
    defaults = dict(
        exotel_call_id=f"call-{uuid.uuid4().hex[:8]}",
        user_id=property_.user_id,
        property_id=property_.id,
        caller_number="+919999999999",
        status="in_progress",
    )
    defaults.update(overrides)
    session = CallSession(**defaults)
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    return session


# 1. MIRA call -> notification created ------------------------------------------------


async def test_mira_call_creates_guest_calling_notification(test_property, db_session, test_user, monkeypatch):
    test_user.phone = "+919812345678"
    await db_session.commit()
    monkeypatch.setattr(twilio_client, "send_whatsapp_best_effort", lambda *a, **k: None)

    session = await _call_session_for(db_session, test_property)
    await guest_calling_notification.maybe_notify_guest_calling(
        test_property.id, session.id, "+919999999999"
    )

    notifications = await list_notifications(db_session)
    matches = [n for n in notifications if n.channel == guest_calling_notification.NOTIFICATION_CHANNEL_GUEST_CALLING]
    assert len(matches) == 1
    assert matches[0].call_session_id == session.id
    assert matches[0].property_id == test_property.id


# 2. HOST call -> no notification -------------------------------------------------------


async def test_host_call_creates_no_guest_calling_notification(db_session, test_user):
    test_property = Property(
        user_id=test_user.id,
        name="Host Mode Villa",
        base_price=1000,
        exophone=f"+9180{uuid.uuid4().int % 10**8:08d}",
        call_handling_mode="HOST",
    )
    db_session.add(test_property)
    await db_session.commit()
    await db_session.refresh(test_property)

    session = await _call_session_for(db_session, test_property)
    await guest_calling_notification.maybe_notify_guest_calling(
        test_property.id, session.id, "+919999999999"
    )

    notifications = await list_notifications(db_session)
    matches = [n for n in notifications if n.channel == guest_calling_notification.NOTIFICATION_CHANNEL_GUEST_CALLING]
    assert matches == []


# 3. Missing host phone -> voice call unaffected (no notification created error) --------


async def test_missing_host_phone_does_not_raise_and_still_creates_in_app_notification(
    test_property, db_session, test_user
):
    test_user.phone = None
    await db_session.commit()

    session = await _call_session_for(db_session, test_property)
    # Must not raise -- the in-app notification (dashboard SSE) still gets
    # created even with no phone to WhatsApp; only the WhatsApp send is
    # skipped.
    await guest_calling_notification.maybe_notify_guest_calling(
        test_property.id, session.id, "+919999999999"
    )

    notifications = await list_notifications(db_session)
    matches = [n for n in notifications if n.channel == guest_calling_notification.NOTIFICATION_CHANNEL_GUEST_CALLING]
    assert len(matches) == 1


# 4. WhatsApp unconfigured -> voice call unaffected --------------------------------------


async def test_whatsapp_unconfigured_does_not_raise(test_property, db_session, test_user, monkeypatch):
    test_user.phone = "+919812345678"
    await db_session.commit()
    from app.config import settings

    monkeypatch.setattr(settings, "twilio_account_sid", None)
    monkeypatch.setattr(settings, "twilio_auth_token", None)
    monkeypatch.setattr(settings, "twilio_guest_calling_template_sid", None)

    session = await _call_session_for(db_session, test_property)
    # send_whatsapp_best_effort itself no-ops cleanly when Twilio isn't
    # configured (see twilio_client.py) -- confirms this function doesn't
    # need its own extra guard beyond "does host.phone exist."
    await guest_calling_notification.maybe_notify_guest_calling(
        test_property.id, session.id, "+919999999999"
    )

    notifications = await list_notifications(db_session)
    matches = [n for n in notifications if n.channel == guest_calling_notification.NOTIFICATION_CHANNEL_GUEST_CALLING]
    assert len(matches) == 1


# 5. WhatsApp failure -> voice call unaffected --------------------------------------------


async def test_whatsapp_failure_does_not_raise(test_property, db_session, test_user, monkeypatch):
    test_user.phone = "+919812345678"
    await db_session.commit()

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated Twilio failure")

    monkeypatch.setattr(twilio_client, "send_whatsapp_best_effort", _boom)

    session = await _call_session_for(db_session, test_property)
    # maybe_notify_guest_calling's own try/except (module-level entry
    # point) must catch this -- the in-app notification write already
    # committed before the WhatsApp send is attempted, so this simulates a
    # failure strictly after the DB write, confirming it doesn't roll
    # anything back or propagate.
    await guest_calling_notification.maybe_notify_guest_calling(
        test_property.id, session.id, "+919999999999"
    )

    notifications = await list_notifications(db_session)
    matches = [n for n in notifications if n.channel == guest_calling_notification.NOTIFICATION_CHANNEL_GUEST_CALLING]
    assert len(matches) == 1


# 6. Correct property association -----------------------------------------------------------


async def test_notification_associated_with_correct_property(db_session, test_user):
    property_a = Property(
        user_id=test_user.id, name="Villa A", base_price=1000, exophone=f"+9180{uuid.uuid4().int % 10**8:08d}"
    )
    property_b = Property(
        user_id=test_user.id, name="Villa B", base_price=1000, exophone=f"+9180{uuid.uuid4().int % 10**8:08d}"
    )
    db_session.add_all([property_a, property_b])
    await db_session.commit()
    await db_session.refresh(property_a)
    await db_session.refresh(property_b)

    session = await _call_session_for(db_session, property_a)
    await guest_calling_notification.maybe_notify_guest_calling(property_a.id, session.id, "+919999999999")

    notifications = await list_notifications(db_session)
    matches = [n for n in notifications if n.channel == guest_calling_notification.NOTIFICATION_CHANNEL_GUEST_CALLING]
    assert len(matches) == 1
    assert matches[0].property_id == property_a.id
    assert matches[0].property_id != property_b.id


# 7. Correct CallSession association ---------------------------------------------------------


async def test_notification_associated_with_correct_call_session(test_property, db_session):
    session_1 = await _call_session_for(db_session, test_property)
    session_2 = await _call_session_for(db_session, test_property)

    await guest_calling_notification.maybe_notify_guest_calling(test_property.id, session_1.id, "+919999999999")

    notifications = await list_notifications(db_session)
    matches = [n for n in notifications if n.channel == guest_calling_notification.NOTIFICATION_CHANNEL_GUEST_CALLING]
    assert len(matches) == 1
    assert matches[0].call_session_id == session_1.id
    assert matches[0].call_session_id != session_2.id


# 8. Lead association -- not attempted here (no Lead exists yet at this point in
# the call lifecycle; see guest_calling_notification.py's own docstring on
# why caller_number, not CallSession.guest_phone/lead, is used). Explicitly
# confirms lead_id stays unset, matching every other channel's default.


async def test_notification_has_no_lead_id_this_early_in_the_call(test_property, db_session, test_user):
    test_user.phone = "+919812345678"
    await db_session.commit()
    session = await _call_session_for(db_session, test_property)
    await guest_calling_notification.maybe_notify_guest_calling(test_property.id, session.id, "+919999999999")

    notifications = await list_notifications(db_session)
    matches = [n for n in notifications if n.channel == guest_calling_notification.NOTIFICATION_CHANNEL_GUEST_CALLING]
    assert matches[0].lead_id is None


# 9. Duplicate pipeline execution -> no duplicate notification ---------------------------------


async def test_duplicate_call_does_not_create_duplicate_notification(test_property, db_session, test_user):
    test_user.phone = "+919812345678"
    await db_session.commit()
    session = await _call_session_for(db_session, test_property)

    await guest_calling_notification.maybe_notify_guest_calling(test_property.id, session.id, "+919999999999")
    # Simulates a reconnect/retry re-entering the pipeline for the SAME
    # exotel_call_id -- get_or_create_call_session would return the same
    # session.id again, so this call site would fire a second time with
    # identical arguments.
    await guest_calling_notification.maybe_notify_guest_calling(test_property.id, session.id, "+919999999999")

    notifications = await list_notifications(db_session)
    matches = [n for n in notifications if n.channel == guest_calling_notification.NOTIFICATION_CHANNEL_GUEST_CALLING]
    assert len(matches) == 1


# 10. Completed call -- this function never reads CallSession.status at all
# (confirmed: only property_id/call_session_id/caller_number are consulted),
# so "don't notify a completed call" isn't enforced by a status branch here
# -- it's guaranteed structurally, because the ONLY two call sites of this
# function in app/voice/pipeline.py sit immediately after CallSession
# creation (status="in_progress" by construction), never anywhere near
# on_pipeline_finished/finalize_call_session (an entirely separate,
# unrelated code path -- confirmed via grep, no shared call chain). This
# test proves the function is genuinely status-agnostic: even a CallSession
# already marked "completed" still gets notified if this function were
# somehow invoked against it, because the real guarantee lives in WHERE
# pipeline.py calls this function, not in a check inside it.


async def test_guest_calling_notification_does_not_discriminate_on_call_session_status(
    test_property, db_session, test_user
):
    test_user.phone = "+919812345678"
    await db_session.commit()
    session = await _call_session_for(db_session, test_property, status="completed")

    await guest_calling_notification.maybe_notify_guest_calling(test_property.id, session.id, "+919999999999")

    notifications = await list_notifications(db_session)
    matches = [n for n in notifications if n.channel == guest_calling_notification.NOTIFICATION_CHANNEL_GUEST_CALLING]
    assert len(matches) == 1


# 11-12. Host phone update authorization -- covered by test_auth.py's existing
# PATCH /auth/me tests (current_user-scoped, no property involved) and this
# phase's own confirmation that no new authorization surface was added.


# 13. Phone validation -- not implemented at this layer; User.phone has no
# format validator today (see app/schemas/user.py), unchanged by this phase
# (see Phase 5's own report for why this is a deliberate non-change).


# Never-raises contract, mirroring recovery_service's own test -----------------------------


async def test_maybe_notify_guest_calling_never_raises_on_internal_failure(test_property, monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr(guest_calling_notification, "_maybe_notify_guest_calling", _boom)

    # Must not raise.
    await guest_calling_notification.maybe_notify_guest_calling(
        test_property.id, uuid.uuid4(), "+919999999999"
    )


async def test_unknown_property_does_not_raise(db_session):
    await guest_calling_notification.maybe_notify_guest_calling(uuid.uuid4(), uuid.uuid4(), "+919999999999")
    notifications = await list_notifications(db_session)
    matches = [n for n in notifications if n.channel == guest_calling_notification.NOTIFICATION_CHANNEL_GUEST_CALLING]
    assert matches == []


async def test_invalid_call_handling_config_does_not_raise_and_skips(db_session, test_user):
    test_property = Property(
        user_id=test_user.id,
        name="Broken Config Villa",
        base_price=1000,
        exophone=f"+9180{uuid.uuid4().int % 10**8:08d}",
        call_handling_mode="SCHEDULED",  # missing schedule_start/end
    )
    db_session.add(test_property)
    await db_session.commit()
    await db_session.refresh(test_property)

    session = await _call_session_for(db_session, test_property)
    await guest_calling_notification.maybe_notify_guest_calling(test_property.id, session.id, "+919999999999")

    notifications = await list_notifications(db_session)
    matches = [n for n in notifications if n.channel == guest_calling_notification.NOTIFICATION_CHANNEL_GUEST_CALLING]
    assert matches == []


# Phase 6: WhatsApp message carries a valid, verifiable Take Call link -----------------


async def test_whatsapp_message_includes_a_valid_take_call_link(test_property, db_session, test_user, monkeypatch):
    """The plain-text fallback message (no Twilio template configured) is
    the easiest place to assert on the actual message body -- confirms the
    take-call URL embedded in it round-trips through verify_take_call_token
    back to the exact call_session_id/property_id/host_user_id this
    notification was fired for."""
    from app.services.take_call_token import verify_take_call_token

    test_user.phone = "+919812345678"
    await db_session.commit()

    captured = {}

    async def _capture(to_phone, body):
        captured["to_phone"] = to_phone
        captured["body"] = body

    monkeypatch.setattr(twilio_client, "send_whatsapp_best_effort", _capture)
    from app.config import settings

    monkeypatch.setattr(settings, "twilio_guest_calling_template_sid", None)

    session = await _call_session_for(db_session, test_property)
    await guest_calling_notification.maybe_notify_guest_calling(test_property.id, session.id, "+919999999999")

    assert captured["to_phone"] == "+919812345678"
    assert "Take the call yourself:" in captured["body"]

    import re

    url_match = re.search(r"https?://\S+", captured["body"])
    assert url_match is not None
    token = url_match.group(0).split("token=", 1)[1]

    call_session_id, property_id, host_user_id = verify_take_call_token(token)
    assert call_session_id == session.id
    assert property_id == test_property.id
    assert host_user_id == test_user.id
