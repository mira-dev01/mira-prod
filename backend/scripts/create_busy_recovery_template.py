"""One-off setup: creates the twilio/text Content Template RecoveryService
sends a guest whose call was rejected as BUSY_RECOVERY (see
app/services/recovery_service.py, app/services/call_coordinator.py) -- a
short "Mira is helping another guest, please call back in a few minutes"
message, matching what the guest is already told out loud on the call
itself (BUSY_MESSAGE_TEXT, app/voice/ringing_audio.py). Run once per Twilio
account/environment.

Usage:
    cd backend && source .venv/bin/activate && python -m scripts.create_busy_recovery_template

Prints the ContentSid to set as TWILIO_BUSY_RECOVERY_TEMPLATE_SID in .env
(local) or the Render dashboard (production) -- then restart the backend.
"""

import asyncio

from app.integrations.twilio_client import create_text_template

# {{1}} = property_name (or a lead-line-specific phrase when the call had no
# single property -- see recovery_service.py's property_label).
_BODY = (
    "Hi! Sorry we missed your call to {{1}} just now -- Mira is helping another guest right now. "
    "Please try calling back in about 5 minutes and we'll be right with you."
)

_VARIABLE_SAMPLES = {"1": "Sample Villa"}


async def main() -> None:
    content_sid = await create_text_template(
        friendly_name="mira_busy_recovery",
        body=_BODY,
        variable_samples=_VARIABLE_SAMPLES,
    )
    print(f"Created ContentSid: {content_sid}")
    print(f"Set TWILIO_BUSY_RECOVERY_TEMPLATE_SID={content_sid} and restart the backend.")


if __name__ == "__main__":
    asyncio.run(main())
