"""The read-only WhatsApp monitor endpoint (Streamlit app whatsapp_simulation.py reads it)."""

from app.config import settings
from app.integrations import twilio_client

_TWILIO_ANSWER = {
    "messages": [
        {"sid": "SM1", "direction": "outbound-api", "from": "whatsapp:+14155550000", "to": "whatsapp:+919876543210",
         "body": "Hi Asha, complete your Spacez booking payment here: https://pay.test/x", "status": "failed",
         "error_code": 63016, "error_message": "Outside the 24h window", "num_media": "0", "date_created": "2026-11-11T10:00:00+00:00"},
        {"sid": "SM2", "direction": "inbound", "from": "whatsapp:+919876543210", "to": "whatsapp:+14155550000",
         "body": "Thanks!", "status": "received", "error_code": None, "error_message": None, "num_media": "1",
         "date_sent": "2026-11-11T10:05:00+00:00", "date_created": "2026-11-11T10:05:01+00:00"},
        {"sid": "SM3", "direction": "outbound-api", "from": "+14155550000", "to": "+919876543210", "body": "an SMS", "status": "sent"},
    ]
}


class _Response:
    status_code = 200

    def json(self):
        return _TWILIO_ANSWER


class _Client:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kwargs):
        assert "/Messages.json" in url
        return _Response()


async def test_twilio_messages_are_mapped_and_sms_is_left_out(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "ACtest")
    monkeypatch.setattr(settings, "twilio_auth_token", "token")
    monkeypatch.setattr(twilio_client.httpx, "AsyncClient", _Client)

    messages = await twilio_client.list_recent_whatsapp_messages(50)

    assert [m["sid"] for m in messages] == ["SM1", "SM2"]
    assert messages[0]["direction"] == "outbound" and messages[0]["to"] == "+919876543210"
    assert messages[0]["status"] == "failed" and messages[0]["error_code"] == 63016
    assert messages[1]["direction"] == "inbound" and messages[1]["num_media"] == 1 and messages[1]["at"].startswith("2026-11-11T10:05")


async def test_the_endpoint_is_off_until_a_token_is_set(client, monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_monitor_token", "")
    assert (await client.get("/api/v1/whatsapp-monitor/messages")).status_code == 404


async def test_the_endpoint_needs_the_token(client, monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_monitor_token", "s3cret")
    assert (await client.get("/api/v1/whatsapp-monitor/messages")).status_code == 401
    assert (await client.get("/api/v1/whatsapp-monitor/messages", headers={"X-Monitor-Token": "wrong"})).status_code == 401


async def test_the_endpoint_returns_twilios_record_with_the_right_token(client, monkeypatch):
    async def fake(limit):
        return [{"sid": "SM1", "status": "failed", "error_code": 63016}]

    monkeypatch.setattr(settings, "whatsapp_monitor_token", "s3cret")
    monkeypatch.setattr(twilio_client, "list_recent_whatsapp_messages", fake)

    response = await client.get("/api/v1/whatsapp-monitor/messages", headers={"X-Monitor-Token": "s3cret"})

    assert response.status_code == 200 and response.json()[0]["error_code"] == 63016


async def test_a_twilio_failure_is_a_502_not_a_crash(client, monkeypatch):
    async def broken(limit):
        raise twilio_client.TwilioError("down")

    monkeypatch.setattr(settings, "whatsapp_monitor_token", "s3cret")
    monkeypatch.setattr(twilio_client, "list_recent_whatsapp_messages", broken)

    assert (await client.get("/api/v1/whatsapp-monitor/messages", headers={"X-Monitor-Token": "s3cret"})).status_code == 502
