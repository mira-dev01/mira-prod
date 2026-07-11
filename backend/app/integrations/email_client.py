"""Thin SMTP wrapper for host-facing email notifications.

Interim stand-in for a WhatsApp Business API host summary (see
app/services/notification_service.py's docstring) -- that path needs Meta
business verification plus Exotel's own KYC/approval, with no instant
sandbox the way Twilio's WhatsApp sandbox works. SMTP was picked over a
vendor email API (SendGrid/Resend/etc.) so no new account is required: any
existing host/business inbox with SMTP access (Gmail app password, Zoho,
Amazon SES SMTP, ...) works as-is.
"""

from email.message import EmailMessage

import aiosmtplib

from app.config import settings


async def send_email(to: str, subject: str, body: str) -> dict:
    if not (settings.smtp_host and settings.smtp_username and settings.smtp_password and settings.smtp_from_email):
        return {"status": "skipped", "reason": "SMTP is not configured"}

    message = EmailMessage()
    message["From"] = settings.smtp_from_email
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password,
        start_tls=settings.smtp_use_tls,
    )
    return {"status": "sent"}
