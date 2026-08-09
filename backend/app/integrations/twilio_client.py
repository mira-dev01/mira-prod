"""Twilio WhatsApp Sandbox client -- real WhatsApp delivery to opted-in
numbers only (see the sandbox caveat in app/config.py's twilio_account_sid
comment). Plain REST + Basic Auth, no twilio SDK dependency, matching the
httpx-direct style of bright_data_client.py/searchapi_client.py.
"""

import logging
import json
import re

import httpx

from app.config import settings
from app.utils.webhook_auth import verify_shared_secret_token

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.twilio.com/2010-04-01"
_CONTENT_BASE_URL = "https://content.twilio.com/v1"


class TwilioError(Exception):
    """Raised for any non-2xx response or unexpected shape from Twilio."""


def verify_whatsapp_webhook_token(token: str | None) -> bool:
    """Same shared-secret-path-token pattern as twilio_voice.verify_voice_webhook_token
    / exotel_client.verify_webhook_token -- simpler than implementing Twilio's
    own X-Twilio-Signature HMAC scheme, consistent with how this codebase
    already secures every other inbound Twilio/Exotel callback."""
    return verify_shared_secret_token(token, settings.twilio_whatsapp_webhook_token)


def _to_whatsapp_address(phone: str) -> str:
    """Twilio wants E.164 with a `whatsapp:` prefix. Numbers coming through
    the voice tools are typically bare 10-digit Indian numbers (see
    _normalize_phone in app/schemas/tool.py) -- default to +91 when no
    country code is present rather than rejecting, since MIRA is India-only
    today."""
    digits = re.sub(r"\D", "", phone)
    if not digits.startswith("91") and len(digits) == 10:
        digits = "91" + digits
    return f"whatsapp:+{digits}"


async def send_whatsapp_message(to_phone: str, body: str, timeout: float = 15.0) -> dict:
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        return {"status": "skipped", "reason": "Twilio is not configured"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{_BASE_URL}/Accounts/{settings.twilio_account_sid}/Messages.json",
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            data={
                "From": settings.twilio_whatsapp_from,
                "To": _to_whatsapp_address(to_phone),
                "Body": body,
            },
        )
        if response.status_code >= 400:
            # Twilio's error body is JSON with `message`/`code` -- surface it
            # verbatim rather than just the status, since the most common
            # failure (63015: recipient never joined the sandbox) is only
            # distinguishable that way.
            raise TwilioError(f"send failed ({response.status_code}): {response.text}")
        data = response.json()

    return {"status": "sent", "sid": data.get("sid"), "twilio_status": data.get("status")}


async def send_whatsapp_best_effort(to_phone: str, body: str, timeout: float = 15.0) -> None:
    """Fire-and-forget wrapper around send_whatsapp_message -- the shared
    "log skip/failure, never raise" contract every caller that sends
    WhatsApp from a detached asyncio.create_task needs (tool_handlers.py's
    escalation/photos/send_whatsapp flows, and recovery_service.py's guest/
    host busy-recovery messages). Previously each caller reimplemented this
    same try/except shape independently; centralized here since it's
    intrinsic to how this client is meant to be used fire-and-forget, not
    specific to any one caller's business logic."""
    try:
        result = await send_whatsapp_message(to_phone, body, timeout=timeout)
        if result.get("status") == "skipped":
            logger.info("WhatsApp message to %s skipped: %s", to_phone, result.get("reason"))
    except TwilioError:
        logger.exception("Failed to send WhatsApp message to %s", to_phone)


async def send_whatsapp_template(
    to_phone: str, content_sid: str, content_variables: dict[str, str], timeout: float = 15.0
) -> dict:
    """Sends a pre-created Content Template (see create_call_to_action_template
    below) instead of a plain Body -- the only way to get a native WhatsApp
    button (custom label, no raw URL text, no link-preview card) rather than
    an auto-linkified URL. content_variables keys are the template's "{{N}}"
    placeholders as strings, e.g. {"1": "🔴", "2": "HIGH"}."""
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        return {"status": "skipped", "reason": "Twilio is not configured"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{_BASE_URL}/Accounts/{settings.twilio_account_sid}/Messages.json",
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            data={
                "From": settings.twilio_whatsapp_from,
                "To": _to_whatsapp_address(to_phone),
                "ContentSid": content_sid,
                "ContentVariables": json.dumps(content_variables),
            },
        )
        if response.status_code >= 400:
            raise TwilioError(f"template send failed ({response.status_code}): {response.text}")
        data = response.json()

    return {"status": "sent", "sid": data.get("sid"), "twilio_status": data.get("status")}


async def send_whatsapp_template_best_effort(
    to_phone: str, content_sid: str, content_variables: dict[str, str], timeout: float = 15.0
) -> None:
    """Fire-and-forget wrapper around send_whatsapp_template -- same
    "log skip/failure, never raise" contract send_whatsapp_best_effort
    gives plain-text sends, for a template send fired from a detached
    asyncio.create_task (e.g. RecoveryService's guest-facing busy-recovery
    menu, app/services/recovery_service.py)."""
    try:
        result = await send_whatsapp_template(to_phone, content_sid, content_variables, timeout=timeout)
        if result.get("status") == "skipped":
            logger.info("WhatsApp template to %s skipped: %s", to_phone, result.get("reason"))
    except TwilioError:
        logger.exception("Failed to send WhatsApp template to %s", to_phone)


async def create_call_to_action_template(
    friendly_name: str, body: str, button_title: str, button_url: str, variable_samples: dict[str, str],
    timeout: float = 15.0,
) -> str:
    """One-off setup call, not used on the hot path -- creates a
    twilio/call-to-action Content Template (a WhatsApp message with a real
    URL button) and returns its ContentSid to be stored in config and reused
    by send_whatsapp_template. See scripts/create_escalation_template.py."""
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        raise TwilioError("Twilio is not configured")

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{_CONTENT_BASE_URL}/Content",
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            json={
                "friendly_name": friendly_name,
                "language": "en",
                "variables": variable_samples,
                "types": {
                    "twilio/call-to-action": {
                        "body": body,
                        "actions": [{"type": "URL", "title": button_title, "url": button_url}],
                    }
                },
            },
        )
        if response.status_code >= 400:
            raise TwilioError(f"template create failed ({response.status_code}): {response.text}")
        data = response.json()

    return data["sid"]


async def create_text_template(
    friendly_name: str, body: str, variable_samples: dict[str, str], timeout: float = 15.0
) -> str:
    """Same one-off provisioning shape as create_call_to_action_template
    above, for a plain twilio/text Content Template -- no button. For a
    message that expects the guest to reply with a typed number/keyword
    (see scripts/create_busy_recovery_template.py) rather than tap a URL, a
    call-to-action button is the wrong content type: WhatsApp's own
    "type-a-reply" affordance already exists without one, and Twilio's
    call-to-action buttons only support URL/PHONE_NUMBER actions, not a
    postback the guest's answer could carry."""
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        raise TwilioError("Twilio is not configured")

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{_CONTENT_BASE_URL}/Content",
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            json={
                "friendly_name": friendly_name,
                "language": "en",
                "variables": variable_samples,
                "types": {"twilio/text": {"body": body}},
            },
        )
        if response.status_code >= 400:
            raise TwilioError(f"template create failed ({response.status_code}): {response.text}")
        data = response.json()

    return data["sid"]
