from app.services import recovery_service


async def test_lead_agent_busy_recovery_notification_visible_via_get_notifications(
    test_user, auth_headers, client, db_session
):
    # Principal-review regression, end-to-end through the real route (not
    # just the service function): a Lead Agent busy-recovery notification
    # must actually show up in GET /notifications, the same endpoint the
    # dashboard's Live Requests / Opportunities panels read from.
    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=test_user.id,
        property_id=None,
        caller_number="+919999999990",
        dialed_number="+911234567890",
    )
    assert metadata is not None

    resp = await client.get("/api/v1/notifications", headers=auth_headers)
    assert resp.status_code == 200
    ids = [n["id"] for n in resp.json()]
    assert str(metadata.notification_id) in ids
