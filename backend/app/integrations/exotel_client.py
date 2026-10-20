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

# In-process, best-effort de-dup for hangup_call: not a correctness
# mechanism (a second process/restart starts with an empty set, and Exotel
# itself tolerates a redundant "Status: completed" POST against an
# already-ended call -- this is not two people getting double-charged, it's
# one wasted HTTP round trip), just avoids firing the same REST call twice
# for the same call_sid when two teardown paths in this process legitimately
# race (e.g. the normal on_pipeline_finished path and a watchdog/cancellation
# path both reaching hangup_call for the same call). A plain set keyed by
# call_sid, not Redis/DB-backed -- deliberately not the kind of
# cross-process-authoritative state CallCoordinator's lease is; see
# app/services/call_coordinator.py for that. Grows by one short string per
# call for the life of the process, never evicted -- at realistic call
# volumes (thousands/day, not millions/sec) that's a few KB even after a
# very long uptime, not worth TTL/eviction machinery for.
_hangup_requested: set[str] = set()


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
    Twilio-style REST convention Exotel mirrors elsewhere (see send_sms).

    Idempotent per call_sid within this process (see _hangup_requested) --
    a second call for the same call_sid, once the first has actually
    succeeded, is a same-process no-op rather than a second REST round
    trip. A call_sid is marked only on success (see below), deliberately
    not up front -- marking before the request would let a transient
    failure (network error, non-2xx from Exotel) permanently block any
    future retry attempt for that call_sid within this process, which is
    strictly worse than the duplicate-request problem this guard exists to
    avoid."""
    if call_sid in _hangup_requested:
        return {"status": "skipped", "reason": "hangup already requested for this call"}

    if not (settings.exotel_sid and settings.exotel_api_key and settings.exotel_api_token):
        return {"status": "skipped", "reason": "Exotel credentials not configured"}

    url = f"https://{settings.exotel_subdomain}/v1/Accounts/{settings.exotel_sid}/Calls/{call_sid}.json"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, auth=(settings.exotel_api_key, settings.exotel_api_token), data={"Status": "completed"})
        response.raise_for_status()
        _hangup_requested.add(call_sid)
        return response.json()
