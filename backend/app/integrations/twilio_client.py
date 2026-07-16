"""Twilio WhatsApp Sandbox client -- real WhatsApp delivery to opted-in
numbers only (see the sandbox caveat in app/config.py's twilio_account_sid
comment). Plain REST + Basic Auth, no twilio SDK dependency, matching the
httpx-direct style of bright_data_client.py/searchapi_client.py.
"""

import json
import re

import httpx

from app.config import settings

_BASE_URL = "https://api.twilio.com/2010-04-01"
_CONTENT_BASE_URL = "https://content.twilio.com/v1"


class TwilioError(Exception):
    """Raised for any non-2xx response or unexpected shape from Twilio."""


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
