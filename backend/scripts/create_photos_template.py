"""One-off setup: creates the twilio/text Content Template the send_photos
voice tool sends a guest (see app/services/tool_handlers.handle_send_photos)
-- covers both a single property's gallery link and the "photos of all our
properties" portfolio link, since the message shape is identical either
way (just a property label + a URL). No button: the gallery/portfolio URL
is plain text in the body, auto-linkified by WhatsApp, since the URL itself
is per-message dynamic data (a call-to-action button's URL is baked in at
template-creation time and can't vary per send -- see
create_guest_calling_template.py for the same constraint on a different
template).

Usage:
    cd backend && source .venv/bin/activate && python -m scripts.create_photos_template

Prints the ContentSid to set as TWILIO_PHOTOS_TEMPLATE_SID in .env (local)
or the Render dashboard (production) -- then restart the backend.
"""

import asyncio

from app.integrations.twilio_client import create_text_template

# {{1}} = property name, or "all our properties" for the portfolio case.
# {{2}} = the gallery/portfolio URL.
_BODY = "Here are photos of {{1}} -- {{2}}"

_VARIABLE_SAMPLES = {"1": "Sample Villa", "2": "https://app.example.com/p/00000000-0000-0000-0000-000000000000/photos"}


async def main() -> None:
    content_sid = await create_text_template(
        friendly_name="mira_photos",
        body=_BODY,
        variable_samples=_VARIABLE_SAMPLES,
    )
    print(f"Created ContentSid: {content_sid}")
    print(f"Set TWILIO_PHOTOS_TEMPLATE_SID={content_sid} and restart the backend.")


if __name__ == "__main__":
    asyncio.run(main())
