"""One-off setup: creates the twilio/call-to-action Content Template for the
host-facing end-of-call summary (see app/services/call_summary_notification.py,
called from app/voice/pipeline.py's on_pipeline_finished once a real call
ends). Same shape as create_escalation_template.py -- a "View Calls" button
whose URL is baked in at creation time (Twilio's Content API has no
per-message variable substitution inside a button's own URL field, same
caveat as create_guest_calling_template.py), pointing at the general Calls
list. The specific call's own dashboard link is carried in the body instead,
as plain text ({{8}}) -- WhatsApp auto-links any https:// URL in a text
body, same technique create_guest_calling_template.py uses for its Take
Call link.

Run once per Twilio account/environment; re-run and update
TWILIO_CALL_SUMMARY_TEMPLATE_SID whenever FRONTEND_BASE_URL changes (the
button's URL is baked in at creation time, not passed per-message).

Usage:
    cd backend && source .venv/bin/activate && python -m scripts.create_call_summary_template

Prints the ContentSid to set as TWILIO_CALL_SUMMARY_TEMPLATE_SID in .env
(local) or the Render dashboard (production) -- then restart the backend.
"""

import asyncio

from app.config import settings
from app.integrations.twilio_client import create_call_to_action_template

_BODY = (
    "\U0001F4DE *Call summary*\n"
    "*Property:* {{1}}\n"
    "*Guest:* {{2}}\n"
    "*Guests:* {{3}}\n"
    "*Check-in:* {{4}}\n"
    "*Check-out:* {{5}}\n"
    "*Escalation raised:* {{6}}\n\n"
    "{{7}}\n\n"
    "{{8}}"
)

_VARIABLE_SAMPLES = {
    "1": "Sample Villa",
    "2": "Riya Sharma",
    "3": "4",
    "4": "2026-09-10",
    "5": "2026-09-13",
    "6": "No",
    "7": "Guest asked about weekend availability and pricing; confirmed interest, awaiting host follow-up.",
    "8": "https://app.example.com/dashboard/calls/00000000-0000-0000-0000-000000000000",
}


async def main() -> None:
    dashboard_url = f"{settings.frontend_base_url}/dashboard/calls"
    print(f"Creating template with button URL: {dashboard_url}")
    content_sid = await create_call_to_action_template(
        friendly_name="mira_call_summary",
        body=_BODY,
        button_title="View Calls",
        button_url=dashboard_url,
        variable_samples=_VARIABLE_SAMPLES,
    )
    print(f"\nCreated ContentSid: {content_sid}")
    print(f"Set TWILIO_CALL_SUMMARY_TEMPLATE_SID={content_sid} and restart the backend.")


if __name__ == "__main__":
    asyncio.run(main())
