"""One-off setup: creates the twilio/call-to-action Content Template that
gives escalation WhatsApp messages a real "Go to Dashboard" button instead
of a raw, auto-linkified URL (see app/integrations/twilio_client.py and
tool_handlers.handle_escalate_to_host). Run once per Twilio account/environment
whenever FRONTEND_BASE_URL changes (the button's URL is baked in at
creation time, not passed per-message).

Usage:
    cd backend && source .venv/bin/activate && python -m scripts.create_escalation_template

Prints the ContentSid to paste into ESCALATION_SID in app/services/whatsapp_templates.py
("PASTE THE SIDs HERE"), then submit the template for WhatsApp approval in the
Twilio console (this script only creates it) and redeploy.
"""

import asyncio

from app.config import settings
from app.integrations.twilio_client import create_call_to_action_template

# WhatsApp rejects a body that starts or ends with a {{n}}, hence the fixed text around the placeholders.
_BODY = (
    "Mira alert: {{1}} *{{2}} ESCALATION*\n*Property:* {{3}}\n*Issue:* {{4}}\n*Summary:* {{5}}\n*Guest:* {{6}}\n\n"
    "Open your Mira dashboard to follow up."
)

_VARIABLE_SAMPLES = {
    "1": "🔴",
    "2": "HIGH",
    "3": "Sample Villa",
    "4": "AC not working",
    "5": "Guest checked in today",
    "6": "9876543210",
}


async def main() -> None:
    dashboard_url = f"{settings.frontend_base_url}/dashboard/leads"
    print(f"Creating template with button URL: {dashboard_url}")
    content_sid = await create_call_to_action_template(
        friendly_name="mira_escalation",
        body=_BODY,
        button_title="Go to Dashboard",
        button_url=dashboard_url,
        variable_samples=_VARIABLE_SAMPLES,
    )
    print(f"\nCreated ContentSid: {content_sid}")
    print("Paste it into ESCALATION_SID in app/services/whatsapp_templates.py, submit the template for WhatsApp approval in the Twilio console, and redeploy.")


if __name__ == "__main__":
    asyncio.run(main())
