"""One-off setup: creates the twilio/call-to-action Content Template for
Phase 5's "guest is calling Mira right now" host notification (see
app/services/guest_calling_notification.py). Same shape as
create_escalation_template.py -- a "View in Dashboard" button (static URL,
baked in at creation time, not passed per-message -- Twilio's Content API
has no per-message variable substitution inside a button's own URL field).

Phase 6 addition: the message BODY now also carries the actual, per-call
Take Call link as plain text ({{3}}) -- WhatsApp auto-links any https://
URL appearing in a text body, so this is how a dynamic, signed, single-use
link reaches the host even though the button itself can't carry it. This
is a body-content change from Phase 5's original template, so re-running
this script mints a NEW ContentSid -- TWILIO_GUEST_CALLING_TEMPLATE_SID
must be updated to the new value, the same "re-run whenever the template
needs to change" discipline this script's docstring already documented for
FRONTEND_BASE_URL changes.

Usage:
    cd backend && source .venv/bin/activate && python -m scripts.create_guest_calling_template

Prints the ContentSid to set as TWILIO_GUEST_CALLING_TEMPLATE_SID in .env
(local) or the Render dashboard (production) -- then restart the backend.
"""

import asyncio

from app.config import settings
from app.integrations.twilio_client import create_call_to_action_template

_BODY = (
    "📞 *Guest is calling Mira*\n*Property:* {{1}}\n*Guest:* {{2}}\n\n"
    "Mira is currently handling the call.\n\nTake the call yourself: {{3}}"
)

_VARIABLE_SAMPLES = {
    "1": "Sample Villa",
    "2": "9876543210",
    "3": "https://api.example.com/api/v1/take-call?token=sample",
}


async def main() -> None:
    dashboard_url = f"{settings.frontend_base_url}/dashboard/calls"
    print(f"Creating template with button URL: {dashboard_url}")
    content_sid = await create_call_to_action_template(
        friendly_name="mira_guest_calling",
        body=_BODY,
        button_title="View in Dashboard",
        button_url=dashboard_url,
        variable_samples=_VARIABLE_SAMPLES,
    )
    print(f"\nCreated ContentSid: {content_sid}")
    print(f"Set TWILIO_GUEST_CALLING_TEMPLATE_SID={content_sid} and restart the backend.")


if __name__ == "__main__":
    asyncio.run(main())
