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
    return {"status": "sent"}
