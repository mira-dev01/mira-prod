from app.models.notification import Notification
from app.services import lead_service, notification_service, recovery_service, whatsapp_reply_service


async def test_busy_calls_counts_one_per_rejection_not_per_lead(test_property, auth_headers, client, db_session):
    # Two rejections from the same guest reuse the same open Lead
    # (recovery_service's own dedup), but each is still a real rejected
    # call attempt -- Busy Calls must count 2, not 1.
    await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999911111",
        dialed_number=test_property.exophone,
    )
    await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999911111",
        dialed_number=test_property.exophone,
    )

    resp = await client.get("/api/v1/analytics/recovery", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["busy_calls"] == 2
    assert body["funnel"][0] == {"stage": "busy_calls", "label": "Busy Calls", "value": 2}


async def test_recovered_counts_distinct_leads_not_reply_messages(test_property, auth_headers, client, db_session):
    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999911112",
        dialed_number=test_property.exophone,
    )
    # Two separate replies from the same guest -- must count as one
    # recovered guest, not two. "5"/"talk to host" and free-text replies are
    # the choices that create a busy_recovery_reply notification (property/
    # pricing/faq/brochure auto-reply instead, with no DB write of their
    # own -- see whatsapp_reply_service.handle_inbound_reply).
    await whatsapp_reply_service.handle_inbound_reply(db_session, "whatsapp:+919999911112", "5")
    await whatsapp_reply_service.handle_inbound_reply(db_session, "whatsapp:+919999911112", "is the pool heated?")

    resp = await client.get("/api/v1/analytics/recovery", headers=auth_headers)
    body = resp.json()
    assert body["busy_calls"] == 1
    assert body["recovered"] == 1
    assert body["recovery_rate"] == 1.0

    # Both reply notifications and the original busy notification carry the
    # same lead_id -- the join key the endpoint relies on.
    notifications = await notification_service.list_notifications(db_session)
    lead_ids = {n.lead_id for n in notifications}
    assert lead_ids == {metadata.lead_id}


async def test_converted_and_lost_reflect_lead_status_on_recovery_leads_only(
    test_property, auth_headers, client, test_user, db_session
):
    booked = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999911113",
        dialed_number=test_property.exophone,
    )
    closed = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999911114",
        dialed_number=test_property.exophone,
    )
    lead1 = await lead_service.get_owned_lead(db_session, booked.lead_id, test_user.id)
    lead1.status = "booked"
    lead2 = await lead_service.get_owned_lead(db_session, closed.lead_id, test_user.id)
    lead2.status = "closed"
    await db_session.commit()

    # A normal (non-recovery) booked lead must NOT be counted here -- this
    # metric is scoped to recovery_reason IS NOT NULL leads only.
    ordinary_lead = await lead_service.upsert_lead(db_session, test_user.id, None, guest_name="Ordinary Guest")
    ordinary_lead.status = "booked"
    await db_session.commit()

    resp = await client.get("/api/v1/analytics/recovery", headers=auth_headers)
    body = resp.json()
    assert body["converted"] == 1
    assert body["lost"] == 1


async def test_avg_host_response_uses_responded_at_set_on_first_mark_read(
    test_property, auth_headers, client, db_session
):
    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999911115",
        dialed_number=test_property.exophone,
    )

    resp_before = await client.get("/api/v1/analytics/recovery", headers=auth_headers)
    assert resp_before.json()["avg_host_response_seconds"] is None

    notification = await db_session.get(Notification, metadata.notification_id)
    await notification_service.mark_read(db_session, notification.id)

    resp_after = await client.get("/api/v1/analytics/recovery", headers=auth_headers)
    value = resp_after.json()["avg_host_response_seconds"]
    assert value is not None
    assert value >= 0


async def test_mark_read_twice_does_not_move_responded_at_forward(db_session, test_property):
    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999911116",
        dialed_number=test_property.exophone,
    )
    notification = await db_session.get(Notification, metadata.notification_id)

    first = await notification_service.mark_read(db_session, notification.id)
    first_responded_at = first.responded_at
    assert first_responded_at is not None

    second = await notification_service.mark_read(db_session, notification.id)
    assert second.responded_at == first_responded_at


async def test_avg_recovery_time_measures_first_reply_after_first_rejection(
    test_property, auth_headers, client, db_session
):
    await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999911117",
        dialed_number=test_property.exophone,
    )
    await whatsapp_reply_service.handle_inbound_reply(db_session, "whatsapp:+919999911117", "5")

    resp = await client.get("/api/v1/analytics/recovery", headers=auth_headers)
    value = resp.json()["avg_recovery_time_seconds"]
    assert value is not None
    assert value >= 0


async def test_recovery_analytics_scoped_to_current_user_only(
    test_property, auth_headers, client, test_user, db_session
):
    # A second host's busy-recovery activity must never leak into this
    # host's numbers -- same user-scoping discipline analytics_summary
    # already enforces via Lead.user_id/owned_property_ids.
    import uuid as _uuid

    from app.models.property import Property
    from app.models.user import User

    other_host = User(
        email=f"other-host-{_uuid.uuid4().hex[:8]}@example.com",
        clerk_user_id=f"user_{_uuid.uuid4().hex[:16]}",
        name="Other Host",
    )
    db_session.add(other_host)
    await db_session.flush()
    other_property = Property(
        user_id=other_host.id,
        name="Other Villa",
        city="Goa",
        max_guests=4,
        base_price=1000,
        exophone=f"+9181{_uuid.uuid4().int % 10**8:08d}",
    )
    db_session.add(other_property)
    await db_session.commit()

    await recovery_service.handle_busy_recovery(
        host_user_id=other_host.id,
        property_id=other_property.id,
        caller_number="+919999911118",
        dialed_number=other_property.exophone,
    )

    resp = await client.get("/api/v1/analytics/recovery", headers=auth_headers)
    assert resp.json()["busy_calls"] == 0
