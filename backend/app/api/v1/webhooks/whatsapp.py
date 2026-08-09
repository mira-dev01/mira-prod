"""Inbound Twilio WhatsApp webhook -- receives a guest's reply to a Busy
Recovery menu (see app/services/recovery_service.py,
scripts/create_busy_recovery_template.py). Same shared-secret path/query
token pattern as app/api/v1/webhooks/exotel.py (not Twilio's own
X-Twilio-Signature HMAC scheme) and the same thin-dispatch-to-a-service-
function shape, but NOT identical body parsing: Twilio's own inbound-message
webhook contract is always application/x-www-form-urlencoded (fixed by the
platform, not configurable), unlike Exotel's callback, which this account's
Exotel App config can send as either form-encoded or JSON -- see exotel.py's
own content-type branch. A JSON branch here would be dead code for a shape
Twilio will never actually send. Configured as this account's WhatsApp
sandbox "WHEN A MESSAGE COMES IN" webhook URL in the Twilio console.
"""

import logging

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.integrations.twilio_client import verify_whatsapp_webhook_token
from app.services import whatsapp_reply_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])


@router.post("/inbound")
async def whatsapp_inbound(
    request: Request,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not verify_whatsapp_webhook_token(token):
        logger.warning("Rejected inbound WhatsApp webhook with invalid/missing token")
        return {"error": "unauthorized"}

    data = dict(await request.form())
    from_number = data.get("From")
    body = data.get("Body")
    if not from_number or body is None:
        logger.warning("Inbound WhatsApp webhook missing From/Body: %s", data)
        return {"status": "ignored"}

    await whatsapp_reply_service.handle_inbound_reply(db, from_number, body)
    return {"status": "ok"}
