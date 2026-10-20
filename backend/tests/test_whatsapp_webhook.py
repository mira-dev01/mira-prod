from app.services import recovery_service, whatsapp_reply_service


async def test_rejects_missing_token(client):
    resp = await client.post("/api/v1/webhooks/whatsapp/inbound", data={"From": "whatsapp:+919999999977", "Body": "1"})
    assert resp.json() == {"error": "unauthorized"}


async def test_rejects_wrong_token(client):
    resp = await client.post(
        "/api/v1/webhooks/whatsapp/inbound?token=wrong",
        data={"From": "whatsapp:+919999999977", "Body": "1"},
    )
    assert resp.json() == {"error": "unauthorized"}


async def test_missing_from_or_body_ignored(client):
    resp = await client.post("/api/v1/webhooks/whatsapp/inbound?token=test-token", data={"Body": "1"})
    assert resp.json() == {"status": "ignored"}

    resp2 = await client.post(
        "/api/v1/webhooks/whatsapp/inbound?token=test-token", data={"From": "whatsapp:+919999999977"}
    )
    assert resp2.json() == {"status": "ignored"}


async def test_accepts_valid_token_with_no_matching_lead(client):
    # No recovery lead exists for this number -- still a valid, accepted
    # webhook call (handle_inbound_reply no-ops internally), not an error.
    resp = await client.post(
        "/api/v1/webhooks/whatsapp/inbound?token=test-token",
        data={"From": "whatsapp:+910000000000", "Body": "1"},
    )
    assert resp.json() == {"status": "ok"}


async def test_accepts_valid_token_with_matching_lead(client, test_property, db_session):
    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999999966",
        dialed_number=test_property.exophone,
    )
    assert metadata is not None

    resp = await client.post(
        "/api/v1/webhooks/whatsapp/inbound?token=test-token",
        data={"From": "whatsapp:+919999999966", "Body": "1"},
    )
    assert resp.json() == {"status": "ok"}


async def test_internal_failure_never_500s_twilio(client, test_property, db_session, monkeypatch):
    # Principal-review regression: an unhandled exception anywhere in the
    # reply-handling chain must not surface as a 500 to Twilio -- Twilio
    # retries a failed webhook delivery, which without this guard could
    # duplicate a host notification/WhatsApp send for the same guest reply.
    metadata = await recovery_service.handle_busy_recovery(
        host_user_id=test_property.user_id,
        property_id=test_property.id,
        caller_number="+919999999955",
        dialed_number=test_property.exophone,
    )
    assert metadata is not None

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure mid-reply-handling")

    monkeypatch.setattr(whatsapp_reply_service, "_handle_inbound_reply", _boom)

    resp = await client.post(
        "/api/v1/webhooks/whatsapp/inbound?token=test-token",
        data={"From": "whatsapp:+919999999955", "Body": "1"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
