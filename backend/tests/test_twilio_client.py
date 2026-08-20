from app.integrations import twilio_client


async def test_send_whatsapp_template_best_effort_never_raises_when_skipped():
    # Twilio unconfigured in the test env -- send_whatsapp_template itself
    # returns {"status": "skipped"}; confirms the wrapper doesn't misbehave.
    await twilio_client.send_whatsapp_template_best_effort("+919999999999", "HX123", {"1": "value"})


async def test_send_whatsapp_template_best_effort_logs_and_swallows_twilio_error(monkeypatch):
    async def _boom(to_phone, content_sid, content_variables, timeout=15.0):
        raise twilio_client.TwilioError("template send failed (400): bad request")

    monkeypatch.setattr(twilio_client, "send_whatsapp_template", _boom)

    await twilio_client.send_whatsapp_template_best_effort("+919999999999", "HX123", {"1": "value"})


async def test_send_whatsapp_template_best_effort_succeeds_silently(monkeypatch):
    calls = []

    async def _fake_send(to_phone, content_sid, content_variables, timeout=15.0):
        calls.append((to_phone, content_sid, content_variables))
        return {"status": "sent", "sid": "SM123", "twilio_status": "queued"}

    monkeypatch.setattr(twilio_client, "send_whatsapp_template", _fake_send)

    await twilio_client.send_whatsapp_template_best_effort("+919999999999", "HX123", {"1": "value"})

    assert calls == [("+919999999999", "HX123", {"1": "value"})]


async def test_create_text_template_raises_when_twilio_not_configured():
    # Twilio unconfigured in the test env -- same "raise, not skip" contract
    # create_call_to_action_template already has for provisioning calls
    # (these run once, manually, not from a live request path, so failing
    # loudly is correct here unlike the send_* functions above).
    try:
        await twilio_client.create_text_template("mira_test", "body {{1}}", {"1": "sample"})
        assert False, "expected TwilioError"
    except twilio_client.TwilioError:
        pass


async def test_send_whatsapp_best_effort_never_raises_when_skipped(monkeypatch):
    # settings.twilio_account_sid/auth_token are unset in the test env
    # (see conftest.py), so send_whatsapp_message itself already returns
    # {"status": "skipped"} -- this just confirms the wrapper doesn't turn
    # that into an exception or otherwise misbehave.
    await twilio_client.send_whatsapp_best_effort("+919999999999", "hello")


async def test_send_whatsapp_best_effort_logs_and_swallows_twilio_error(monkeypatch):
    async def _boom(to_phone, body, timeout=15.0):
        raise twilio_client.TwilioError("send failed (400): bad request")

    monkeypatch.setattr(twilio_client, "send_whatsapp_message", _boom)

    # Must not raise -- this is the whole point of the wrapper (used from
    # asyncio.create_task, where an uncaught exception vanishes into
    # asyncio's default handler with no caller ever seeing it).
    await twilio_client.send_whatsapp_best_effort("+919999999999", "hello")


async def test_send_whatsapp_best_effort_succeeds_silently(monkeypatch):
    calls = []

    async def _fake_send(to_phone, body, timeout=15.0):
        calls.append((to_phone, body))
        return {"status": "sent", "sid": "SM123", "twilio_status": "queued"}

    monkeypatch.setattr(twilio_client, "send_whatsapp_message", _fake_send)

    await twilio_client.send_whatsapp_best_effort("+919999999999", "hello")

    assert calls == [("+919999999999", "hello")]
