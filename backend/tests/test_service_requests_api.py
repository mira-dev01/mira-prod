from app.models.call_session import CallSession
from app.models.notification import Notification


async def _make_guest_support_call(db_session, test_user, test_property, *, room_number="Not mentioned"):
    session = CallSession(
        user_id=test_user.id,
        property_id=test_property.id,
        caller_number="+919999999999",
        status="completed",
        call_type="GUEST_SUPPORT",
        ai_summary={
            "booking_snapshot": {"room_number": room_number},
            "conversation_summary": "Guest asked for extra towels.",
        },
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    return session


async def test_guest_support_call_appears_in_service_requests(client, auth_headers, db_session, test_user, test_property):
    await _make_guest_support_call(db_session, test_user, test_property, room_number="204")

    resp = await client.get("/api/v1/leads/service-requests", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["property_name"] == "Test Villa"
    assert body[0]["room_number"] == "204"
    assert body[0]["message"] == "Guest asked for extra towels."


async def test_lead_agent_call_falls_back_to_ai_summary_property(client, auth_headers, db_session, test_user):
    """A Lead Agent (portfolio-wide) call has no property_id, even when the
    guest was clearly asking about one specific listing -- the Property
    column must fall back to the AI summary's own extraction instead of
    showing blank."""
    session = CallSession(
        user_id=test_user.id,
        property_id=None,
        caller_number="+919999999999",
        status="completed",
        call_type="GUEST_SUPPORT",
        ai_summary={
            "booking_snapshot": {"property": ["Nile w/pool & projector"], "room_number": "Not mentioned"},
            "conversation_summary": "Guest asked about AC maintenance for Nile.",
        },
    )
    db_session.add(session)
    await db_session.commit()

    resp = await client.get("/api/v1/leads/service-requests", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["property_id"] is None
    assert body[0]["property_name"] == "Nile w/pool & projector"


async def test_booking_lead_call_is_excluded_from_service_requests(client, auth_headers, db_session, test_user, test_property):
    session = CallSession(
        user_id=test_user.id,
        property_id=test_property.id,
        caller_number="+919999999999",
        status="completed",
        call_type="BOOKING_LEAD",
    )
    db_session.add(session)
    await db_session.commit()

    resp = await client.get("/api/v1/leads/service-requests", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_notification_message_takes_priority_over_ai_summary(client, auth_headers, db_session, test_user, test_property):
    session = await _make_guest_support_call(db_session, test_user, test_property)
    notification = Notification(
        property_id=test_property.id,
        call_session_id=session.id,
        channel="escalation",
        urgency="high",
        message="Dispatch requested: general issue -- water bottles",
        status="new",
    )
    db_session.add(notification)
    await db_session.commit()

    resp = await client.get("/api/v1/leads/service-requests", headers=auth_headers)
    body = resp.json()
    assert len(body) == 1
    assert body[0]["message"] == "Dispatch requested: general issue -- water bottles"
    assert body[0]["urgency"] == "high"


async def test_dismiss_moves_request_out_of_live_list_but_keeps_it_for_reference(
    client, auth_headers, db_session, test_user, test_property
):
    session = await _make_guest_support_call(db_session, test_user, test_property)

    live_before = (await client.get("/api/v1/leads/service-requests", headers=auth_headers)).json()
    assert len(live_before) == 1

    dismiss_resp = await client.post(
        "/api/v1/leads/service-requests/dismiss",
        headers=auth_headers,
        json={"call_session_ids": [str(session.id)]},
    )
    assert dismiss_resp.status_code == 200
    assert dismiss_resp.json() == {"dismissed": 1}

    live_after = (await client.get("/api/v1/leads/service-requests", headers=auth_headers)).json()
    assert live_after == []

    with_dismissed = (
        await client.get("/api/v1/leads/service-requests?include_dismissed=true", headers=auth_headers)
    ).json()
    assert len(with_dismissed) == 1
    assert with_dismissed[0]["dismissed_at"] is not None


async def test_dismiss_is_scoped_to_the_requesting_host(client, db_session, test_user, test_property):
    import uuid

    from app.models.user import User
    from tests.conftest import auth_headers_for

    other_user = User(
        email=f"host-{uuid.uuid4().hex[:8]}@example.com",
        clerk_user_id=f"user_{uuid.uuid4().hex[:16]}",
        name="Other Host",
    )
    db_session.add(other_user)
    await db_session.commit()

    session = await _make_guest_support_call(db_session, test_user, test_property)

    resp = await client.post(
        "/api/v1/leads/service-requests/dismiss",
        headers=auth_headers_for(other_user),
        json={"call_session_ids": [str(session.id)]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"dismissed": 0}

    live = (
        await client.get("/api/v1/leads/service-requests", headers=auth_headers_for(test_user))
    ).json()
    assert len(live) == 1
