import aiosmtplib

from app.integrations import email_client


def _configure_smtp(monkeypatch):
    monkeypatch.setattr(email_client.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(email_client.settings, "smtp_port", 587)
    monkeypatch.setattr(email_client.settings, "smtp_username", "user")
    monkeypatch.setattr(email_client.settings, "smtp_password", "pass")
    monkeypatch.setattr(email_client.settings, "smtp_from_email", "mira@example.com")
    monkeypatch.setattr(email_client.settings, "smtp_use_tls", True)


async def test_send_email_skipped_when_smtp_not_configured(monkeypatch):
    monkeypatch.setattr(email_client.settings, "smtp_host", None)
    result = await email_client.send_email("host@example.com", "Subject", "Body")
    assert result == {"status": "skipped", "reason": "SMTP is not configured"}


async def test_send_email_passes_a_bounded_timeout_to_aiosmtplib(monkeypatch):
    """Scale Readiness ("Phase 17"): aiosmtplib.send's own default is 60s --
    confirms the call site now passes an explicit, shorter timeout rather
    than relying on that default."""
    _configure_smtp(monkeypatch)
    captured = {}

    async def _fake_send(message, **kwargs):
        captured.update(kwargs)
        return None, {}

    monkeypatch.setattr(aiosmtplib, "send", _fake_send)

    result = await email_client.send_email("host@example.com", "Subject", "Body")

    assert result == {"status": "sent"}
    assert captured["timeout"] == 15.0


async def test_send_email_timeout_is_overridable(monkeypatch):
    _configure_smtp(monkeypatch)
    captured = {}

    async def _fake_send(message, **kwargs):
        captured.update(kwargs)
        return None, {}

    monkeypatch.setattr(aiosmtplib, "send", _fake_send)

    await email_client.send_email("host@example.com", "Subject", "Body", timeout=5.0)

    assert captured["timeout"] == 5.0
