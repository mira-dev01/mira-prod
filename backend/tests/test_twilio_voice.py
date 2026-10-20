from app.config import settings
from app.integrations import twilio_voice


def test_verify_voice_webhook_token_accepts_matching_token(monkeypatch):
    monkeypatch.setattr(settings, "twilio_voice_webhook_token", "real-secret")
    assert twilio_voice.verify_voice_webhook_token("real-secret") is True


def test_verify_voice_webhook_token_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(settings, "twilio_voice_webhook_token", "real-secret")
    assert twilio_voice.verify_voice_webhook_token("wrong") is False


def test_verify_voice_webhook_token_rejects_missing_token(monkeypatch):
    monkeypatch.setattr(settings, "twilio_voice_webhook_token", "real-secret")
    assert twilio_voice.verify_voice_webhook_token(None) is False
    assert twilio_voice.verify_voice_webhook_token("") is False


def test_build_connect_stream_twiml_includes_stream_url_and_params():
    twiml = twilio_voice.build_connect_stream_twiml(
        "wss://backend.example.com/api/v1/voice/twilio/ws/tok123", "+919876543210", "+912246184496"
    )
    assert '<Stream url="wss://backend.example.com/api/v1/voice/twilio/ws/tok123">' in twiml
    assert '<Parameter name="from_number" value="+919876543210"/>' in twiml
    assert '<Parameter name="to_number" value="+912246184496"/>' in twiml
    assert twiml.startswith('<?xml version="1.0" encoding="UTF-8"?>')


def test_build_connect_stream_twiml_escapes_xml_special_characters():
    # A raw "&"/"<" in a value would produce invalid, unparseable XML if not
    # escaped -- assert the output actually parses as well-formed XML rather
    # than just eyeballing substrings.
    import xml.etree.ElementTree as ET

    twiml = twilio_voice.build_connect_stream_twiml(
        "wss://backend.example.com/ws?a=1&b=2", "Guest & Co <test>", "+912246184496"
    )
    root = ET.fromstring(twiml)  # raises if malformed
    params = root.findall(".//Parameter")
    assert params[0].get("value") == "Guest & Co <test>"


def test_build_reject_twiml_says_and_hangs_up():
    twiml = twilio_voice.build_reject_twiml()
    assert "<Hangup/>" in twiml
    assert "<Say>" in twiml
