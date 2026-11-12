"""Read-only view of the WhatsApp messages Mira's Twilio number sent and received, for the Streamlit monitor
(whatsapp_simulation.py at the repo root).

It shows Twilio's own record, so it is the truth about delivery: a message Twilio accepted and then could not
deliver appears as failed/undelivered with its error code (63016 = outside WhatsApp's 24-hour window, the reason
a payment link needs an approved template). Nothing is stored here and nothing can be changed through it.

Guest phone numbers and message text are in the response, so it needs the shared secret in WHATSAPP_MONITOR_TOKEN
(sent as the X-Monitor-Token header) and answers 404 while that is unset.
"""

import hmac

from fastapi import APIRouter, Header, HTTPException, Query, status

from app.config import settings
from app.integrations import twilio_client

router = APIRouter(prefix="/whatsapp-monitor", tags=["whatsapp-monitor"])


@router.get("/messages")
async def recent_messages(
    limit: int = Query(100, ge=1, le=300),
    x_monitor_token: str | None = Header(default=None),
) -> list[dict]:
    expected = settings.whatsapp_monitor_token
    if not expected:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if not x_monitor_token or not hmac.compare_digest(x_monitor_token.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid monitor token")
    try:
        return await twilio_client.list_recent_whatsapp_messages(limit)
    except twilio_client.TwilioError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Twilio did not answer: {exc}")
