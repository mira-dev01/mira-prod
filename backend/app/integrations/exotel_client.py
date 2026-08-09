"""Thin wrapper around Exotel's REST API.

The actual voice conversation runs through app/api/v1/voice.py and
app/voice/pipeline.py (a websocket the Exotel Voicebot Applet streams audio
to), not through this client. This client only covers what we call from app
code: optional SMS fallback and webhook-token verification for Exotel's
call-status callbacks (also reused to verify the voice websocket's token).
"""

import httpx

from app.config import settings
from app.utils.webhook_auth import verify_shared_secret_token


def verify_webhook_token(token: str | None) -> bool:
    """Exotel does not HMAC-sign callbacks, so we require a shared secret
    token configured as a query param on the callback URL itself."""
    return verify_shared_secret_token(token, settings.exotel_webhook_token)


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


async def hangup_call(call_sid: str) -> dict:
    """Force-terminates a live call via Exotel's Calls API. Closing our end
    of the Voicebot Applet's WebSocket stream only stops the streaming
    portion -- confirmed live, guests were left connected on a silent line
    after Mira's own closing line finished, because nothing was actually
    telling Exotel's platform to hang up the underlying PSTN call. Same
    Twilio-style REST convention Exotel mirrors elsewhere (see send_sms)."""
    if not (settings.exotel_sid and settings.exotel_api_key and settings.exotel_api_token):
        return {"status": "skipped", "reason": "Exotel credentials not configured"}

    url = f"https://{settings.exotel_subdomain}/v1/Accounts/{settings.exotel_sid}/Calls/{call_sid}.json"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, auth=(settings.exotel_api_key, settings.exotel_api_token), data={"Status": "completed"})
        response.raise_for_status()
        return response.json()
