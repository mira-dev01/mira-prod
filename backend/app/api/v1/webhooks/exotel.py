"""Exotel call-status/passthru callback.

The actual voice conversation runs through app/api/v1/voice.py (a websocket
the Exotel Voicebot Applet streams audio to). This webhook only receives
Exotel's own call lifecycle callback (configured as a Passthru/StatusCallback
applet alongside the Voicebot applet in the Exotel call flow), used for
call_sessions logging, recording URLs, and detecting calls that never reached
the AI (busy/no-answer/failed).
"""

import logging

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.integrations.exotel_client import verify_webhook_token
from app.services import call_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/exotel", tags=["webhooks"])


@router.post("/call-status")
async def exotel_call_status(
    request: Request,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not verify_webhook_token(token):
        logger.warning("Rejected Exotel webhook with invalid/missing token")
        return {"error": "unauthorized"}

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
    else:
        data = dict(await request.form())

    call_sid = data.get("CallSid") or data.get("Sid") or data.get("CallSidLeg1")
    if not call_sid:
        logger.warning("Exotel callback missing CallSid: %s", data)
        return {"status": "ignored"}

    await call_service.attach_exotel_call(
        db,
        exotel_call_id=call_sid,
        caller_number=data.get("From"),
        dialed_number=data.get("To"),
        status=data.get("Status") or data.get("DialCallStatus"),
        recording_url=data.get("RecordingUrl"),
    )
    return {"status": "ok"}
