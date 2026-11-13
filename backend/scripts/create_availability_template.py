"""One-off setup: creates the twilio/text Content Template
RecoveryService.process_availability_recovery sends a busy-recovery guest
once Mira's CallCoordinator lease for their host/property is actually
released (see app/services/recovery_service.py, app/services/
call_coordinator.py). Run once per Twilio account/environment.

Usage:
    cd backend && source .venv/bin/activate && python -m scripts.create_availability_template

Prints the ContentSid to paste into AVAILABILITY_SID in app/services/whatsapp_templates.py
("PASTE THE SIDs HERE"), then submit the template for WhatsApp approval in the
Twilio console (this script only creates it) and redeploy.
"""

import asyncio

from app.integrations.twilio_client import create_text_template

_BODY = "Hi! \U0001F44B Mira is available now.\n\nYou can call back whenever you're ready."


async def main() -> None:
    content_sid = await create_text_template(
        friendly_name="mira_availability",
        body=_BODY,
        variable_samples={},
    )
    print(f"Created ContentSid: {content_sid}")
    print("Paste it into AVAILABILITY_SID in app/services/whatsapp_templates.py, submit the template for WhatsApp approval in the Twilio console, and redeploy.")


if __name__ == "__main__":
    asyncio.run(main())
