"""One-off setup: creates the twilio/text Content Template RecoveryService
sends a guest whose call was rejected as BUSY_RECOVERY (see
app/services/recovery_service.py, app/services/call_coordinator.py) -- a
numbered menu the guest replies to with a number/keyword, handled by the
inbound webhook at app/api/v1/webhooks/whatsapp.py /
app/services/whatsapp_reply_service.py. Run once per Twilio account/
environment.

Usage:
    cd backend && source .venv/bin/activate && python -m scripts.create_busy_recovery_template

Prints the ContentSid to set as TWILIO_BUSY_RECOVERY_TEMPLATE_SID in .env
(local) or the Render dashboard (production) -- then restart the backend.
"""

import asyncio

from app.integrations.twilio_client import create_text_template
from app.services.whatsapp_reply_service import MENU_DISPLAY_TEXT

# {{1}} = property_name (or a lead-line-specific phrase when the call had no
# single property -- see recovery_service.py). MENU_DISPLAY_TEXT
# (whatsapp_reply_service.py) is the single source of truth for the numbered
# options, imported here rather than hand-copied, so this template body can
# never silently drift from what _parse_menu_choice actually routes.
_BODY = (
    "Hi! Sorry we missed your call to {{1}} just now -- our line was busy with another guest.\n\n"
    f"Reply with a number and we'll help right away:\n{MENU_DISPLAY_TEXT}"
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
