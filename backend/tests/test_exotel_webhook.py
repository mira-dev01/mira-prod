async def test_rejects_missing_token(client):
    resp = await client.post("/api/v1/webhooks/exotel/call-status", data={"CallSid": "x"})
    assert resp.json() == {"error": "unauthorized"}


async def test_rejects_wrong_token(client):
    resp = await client.post(
        "/api/v1/webhooks/exotel/call-status?token=wrong", data={"CallSid": "x"}
    )
    assert resp.json() == {"error": "unauthorized"}


async def test_accepts_valid_token_and_creates_session(client, test_property):
    resp = await client.post(
        "/api/v1/webhooks/exotel/call-status?token=test-token",
        data={
            "CallSid": "exo_1",
            "From": "+919812345678",
            "To": test_property.exophone,
            "Status": "completed",
            "RecordingUrl": "https://example.com/rec.mp3",
        },
    )
    assert resp.json() == {"status": "ok"}


async def test_missing_call_sid_ignored(client):
    resp = await client.post("/api/v1/webhooks/exotel/call-status?token=test-token", data={"From": "+91123"})
    assert resp.json() == {"status": "ignored"}
