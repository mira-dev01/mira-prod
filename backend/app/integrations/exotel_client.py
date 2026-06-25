"""Thin wrapper around Exotel's REST API.

Voice itself is bridged Exotel -> Vapi directly over a SIP trunk (see
scripts/setup_vapi_assistant.py and README), so our backend is not in the
audio path. This client only covers what we call from app code: optional SMS
fallback and webhook-token verification for Exotel's call-status callbacks.
"""

import hmac

import httpx

from app.config import settings


def verify_webhook_token(token: str | None) -> bool:
    """Exotel does not HMAC-sign callbacks, so we require a shared secret
    token configured as a query param on the callback URL itself."""
    if not token:
        return False
    return hmac.compare_digest(token, settings.exotel_webhook_token)


async def send_sms(to: str, body: str) -> dict:
    if not (settings.exotel_sid and settings.exotel_api_key and settings.exotel_api_token):
        return {"status": "skipped", "reason": "Exotel credentials not configured"}

    url = f"https://{settings.exotel_subdomain}/v1/Accounts/{settings.exotel_sid}/Sms/send.json"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            url,
            auth=(settings.exotel_api_key, settings.exotel_api_token),
            data={"From": settings.exotel_sid, "To": to, "Body": body},
        )
        response.raise_for_status()
        return response.json()
