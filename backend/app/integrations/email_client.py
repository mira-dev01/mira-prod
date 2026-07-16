"""Thin SMTP wrapper for host-facing email notifications.

Interim stand-in for a WhatsApp Business API host summary (see
app/services/notification_service.py's docstring) -- that path needs Meta
business verification plus Exotel's own KYC/approval, with no instant
sandbox the way Twilio's WhatsApp sandbox works. SMTP was picked over a
vendor email API (SendGrid/Resend/etc.) so no new account is required: any
existing host/business inbox with SMTP access (Gmail app password, Zoho,
Amazon SES SMTP, ...) works as-is.

Deliverability note: a plain-text-only EmailMessage with no Date/Message-ID
header and a bare `From` address is a strong spam-heuristic hit on top of
whatever SPF/DKIM/DMARC state the sending domain has. This module now sets
those headers and sends a text+HTML multipart/alternative body, but header
hygiene alone cannot fix an unauthenticated sending domain -- if escalation
emails still land in spam after this change, the SMTP_FROM_EMAIL domain
needs SPF/DKIM (and ideally DMARC) records published, or SMTP_FROM_EMAIL
needs to be an address on a domain that already has them (e.g. the host's
real Gmail/Workspace address via an app password) rather than a fresh
unauthenticated domain.
"""

from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import aiosmtplib

from app.config import settings


async def send_email(to: str, subject: str, body: str, html_body: str | None = None) -> dict:
    if not (settings.smtp_host and settings.smtp_username and settings.smtp_password and settings.smtp_from_email):
        return {"status": "skipped", "reason": "SMTP is not configured"}

    message = EmailMessage()
    message["From"] = f"Mira <{settings.smtp_from_email}>"
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password,
        start_tls=settings.smtp_use_tls,
    )
    return {"status": "sent"}
