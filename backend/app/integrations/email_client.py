<<<<<<< HEAD
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
=======
"""Thin Resend HTTP API wrapper for host-facing email notifications.

Was SMTP (aiosmtplib) until 2026-09-24. Switched after confirming, directly
from inside both the `dev` and `production` Railway containers (a raw
socket connectivity test over `railway ssh`, not just an app-level
timeout), that outbound SMTP ports are blocked at the network level:
connecting to smtp.gmail.com/smtp-relay.gmail.com on 587 or 465 failed
instantly with `OSError: [Errno 101] Network is unreachable` on both
environments, identically, while port 443 (plain HTTPS) connected in under
10ms on both. This is a platform-level anti-abuse policy, not a
credentials/host misconfiguration -- no SMTP provider, host, or app
password could ever have worked here, so switching SMTP providers (as a
prior version of this module's docstring assumed would be the fix for any
delivery problem) would not have helped.

Resend over SendGrid: both were briefly evaluated (a SendGrid version of
this module existed for a few minutes in the same session) -- Resend was
picked because it has a genuinely usable free tier, and the existing
SendGrid account available is a paid one with no need for a second paid
provider for a low-volume host-notification use case.

Deliverability note (carried over from the SMTP version): sender identity
still matters. `RESEND_FROM_EMAIL` should be on a domain Resend has
verified (SPF/DKIM records published via Resend's own domain setup) -- an
unverified sending domain is the most common reason a technically-successful
API call still doesn't result in a delivered email.
"""

import httpx

from app.config import settings
from app.services import usage_meter

_RESEND_URL = "https://api.resend.com/emails"
_DEFAULT_TIMEOUT_SECONDS = 15.0


class ResendError(Exception):
    """Raised for any non-2xx response from Resend's email-send API."""


async def send_email(
    to: str, subject: str, body: str, html_body: str | None = None, timeout: float = _DEFAULT_TIMEOUT_SECONDS
) -> dict:
    if not (settings.resend_api_key and settings.resend_from_email):
        return {"status": "skipped", "reason": "Resend is not configured"}

    payload = {
        "from": f"Mira <{settings.resend_from_email}>",
        "to": [to],
        "subject": subject,
        "text": body,
    }
    if html_body:
        payload["html"] = html_body

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json=payload,
        )
    if response.status_code >= 400:
        raise ResendError(f"send failed ({response.status_code}): {response.text}")
    usage_meter.record_usage_detached("resend", "emails", 1)
>>>>>>> 368377a (20th October)
    return {"status": "sent"}
