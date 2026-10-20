import uuid

from app.models.guest_profile import GuestProfile
from app.models.user import User
from app.services import lead_service, recovery_service
from app.services.notification_service import list_notifications


async def test_handle_busy_recovery_creates_lead_and_notification(test_property, db_session):
    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999999999",
        dialed_number=test_property.exophone,
    )

    assert metadata is not None
    assert metadata.host_user_id == test_property.user_id
    assert metadata.property_id == test_property.id
    assert metadata.caller_number == "+919999999999"

    leads = await lead_service.list_leads(db_session, test_property.user_id)
    assert len(leads) == 1
    assert leads[0].id == metadata.lead_id
    assert leads[0].phone == "+919999999999"
    # recovery_reason explains WHY the lead needed recovery; lead_source
    # (a separate, pre-existing field) is deliberately left at its normal
    # "voice_call" default -- see app/models/lead.py's own comment for the
    # full lead_source/entry_channel/recovery_reason split.
    assert leads[0].recovery_reason == recovery_service.RECOVERY_REASON_BUSY_CALL
    assert leads[0].lead_source == "voice_call"
    assert leads[0].entry_channel == "phone_call"
    assert leads[0].status == "open"

    notifications = await list_notifications(db_session)
    assert any(
        n.id == metadata.notification_id and n.channel == recovery_service.NOTIFICATION_CHANNEL_BUSY_RECOVERY
        for n in notifications
    )


async def test_handle_busy_recovery_creates_guest_profile_scoped_by_phone_and_host(test_property, db_session):
    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999999998",
        dialed_number=test_property.exophone,
    )

    assert metadata.guest_profile_id is not None
    guest = await db_session.get(GuestProfile, metadata.guest_profile_id)
    assert guest is not None
    assert guest.phone == "+919999999998"
    assert guest.host_id == test_property.user_id


async def test_handle_busy_recovery_sets_last_property_id_on_guest_profile(test_property, db_session):
    # Phase 6: a busy-rejected call never reaches guest_memory_service.py
    # (the normal completed-call path that sets this), so RecoveryService
    # must set it directly -- whatsapp_reply_service.py's Property/Pricing/
    # FAQ/Photos menu replies resolve the property through this field.
    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999999989",
        dialed_number=test_property.exophone,
    )

    guest = await db_session.get(GuestProfile, metadata.guest_profile_id)
    assert guest.last_property_id == test_property.id


async def test_handle_busy_recovery_reuses_the_same_lead_across_repeated_rejections(test_property, db_session):
    # A guest busy-rejected more than once must land on the SAME still-open
    # Lead, not fragment into one row per rejected attempt -- this is what
    # "extend the existing Lead lifecycle" means concretely (see
    # lead_service._get_or_create_lead_for_call's guest_profile_id-based
    # reuse, now also reachable with call_session_id=None).
    first = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999999997",
        dialed_number=test_property.exophone,
    )
    second = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999999997",
        dialed_number=test_property.exophone,
    )

    assert first.lead_id == second.lead_id
    leads = await lead_service.list_leads(db_session, test_property.user_id)
    assert len(leads) == 1


async def test_handle_busy_recovery_does_not_reuse_a_booked_lead(test_property, db_session):
    # A guest with an already-booked (resolved) lead getting busy-rejected on
    # a NEW call must start a fresh Lead for the new inquiry, not silently
    # reopen/mutate the resolved one -- same rule real in-call reuse already
    # follows (lead_service._REUSABLE_LEAD_STATUSES).
    first = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999999996",
        dialed_number=test_property.exophone,
    )
    lead = await lead_service.get_owned_lead(db_session, first.lead_id, test_property.user_id)
    lead.status = "booked"
    await db_session.commit()

    second = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999999996",
        dialed_number=test_property.exophone,
    )

    assert second.lead_id != first.lead_id
    leads = await lead_service.list_leads(db_session, test_property.user_id)
    assert len(leads) == 2


async def test_handle_busy_recovery_for_lead_agent_call_has_no_property(test_user, db_session):
    # property_id=None models a Lead Agent line (host-scoped, not
    # property-scoped) -- same convention as CallSession.property_id and
    # CallLease.property_id.
    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=test_user.id,
        property_id=None,
        caller_number="+919999999995",
        dialed_number="+911234567890",
    )

    assert metadata is not None
    assert metadata.property_id is None

    notifications = await list_notifications(db_session)
    matching = [n for n in notifications if n.id == metadata.notification_id]
    assert len(matching) == 1
    assert matching[0].property_id is None


async def test_lead_agent_busy_recovery_notification_is_visible_via_the_real_dashboard_filter(
    test_user, db_session
):
    # Principal-review regression: a Lead Agent (property_id=None,
    # portfolio-wide line) recovery notification was written correctly but
    # then permanently invisible on the dashboard -- GET /notifications and
    # /notifications/stream both called list_notifications with
    # user_property_ids=owned_property_ids(...), and Notification.property_id
    # is NULL for a Lead Agent call, which a property_id.in_(...) filter can
    # never match. Exercises the SAME filter args those two real routes pass
    # (user_property_ids + user_id), not the no-filter call the older test
    # above uses, so this actually proves the dashboard-visible path, not
    # just that the row exists.
    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=test_user.id,
        property_id=None,
        caller_number="+919999999991",
        dialed_number="+911234567890",
    )
    assert metadata is not None

    # test_user owns zero properties -- owned_property_ids(...) would be [].
    # The old bug: property_id.in_([]) matches nothing, ever, for a
    # portfolio-wide notification, regardless of who owns it.
    visible = await list_notifications(db_session, user_property_ids=[], user_id=test_user.id)
    assert any(n.id == metadata.notification_id for n in visible)

    # And the inverse -- a DIFFERENT host must never see it.
    other_host = User(
        email=f"other-{uuid.uuid4().hex[:8]}@example.com",
        clerk_user_id=f"user_{uuid.uuid4().hex[:16]}",
        name="Other Host",
    )
    db_session.add(other_host)
    await db_session.commit()
    not_visible = await list_notifications(db_session, user_property_ids=[], user_id=other_host.id)
    assert not any(n.id == metadata.notification_id for n in not_visible)


async def test_handle_busy_recovery_handles_anonymous_caller_gracefully(test_property, db_session):
    # No caller_number (stripped/anonymous call) -- must degrade to a
    # guest-less Lead/Notification, never raise, matching escalate_to_host's
    # own tolerance for a missing guest_phone.
    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number=None,
        dialed_number=test_property.exophone,
    )

    assert metadata is not None
    assert metadata.guest_profile_id is None
    assert metadata.caller_number is None

    leads = await lead_service.list_leads(db_session, test_property.user_id)
    assert len(leads) == 1
    assert leads[0].phone is None


async def test_handle_busy_recovery_reads_host_phone_after_earlier_commits(test_property, db_session):
    # Regression coverage: host.phone is read (to decide whether to WhatsApp
    # the host) after get_or_create_guest_profile/upsert_lead/create_
    # notification have each already committed on the same session --
    # exactly the shape that broke app/voice/pipeline.py's property_.id
    # access in an earlier phase (an ORM attribute read after a same-session
    # commit risking an implicit lazy-reload outside a valid greenlet
    # context). Captured as host_phone before those commits now (see
    # _handle_busy_recovery), but this test exists so a future edit that
    # reintroduces a late read is caught by something other than luck.
    host = await db_session.get(User, test_property.user_id)
    host.phone = "9876543210"
    await db_session.commit()

    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999999992",
        dialed_number=test_property.exophone,
    )

    assert metadata is not None


async def test_handle_busy_recovery_returns_none_for_unknown_host(db_session):
    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=uuid.uuid4(),
        property_id=None,
        caller_number="+919999999994",
        dialed_number="+911234567890",
    )
    assert metadata is None


async def test_handle_busy_recovery_never_raises_on_internal_failure(test_property, monkeypatch):
    # handle_busy_recovery is fired via asyncio.create_task from
    # pipeline.py -- an unhandled exception there would vanish into
    # asyncio's default handler with no caller ever seeing it, so this must
    # log and return None instead of propagating, same contract as every
    # other fire-and-forget handler in this codebase.
    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(recovery_service, "_handle_busy_recovery", _boom)

    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999999993",
        dialed_number=test_property.exophone,
    )
    assert metadata is None
