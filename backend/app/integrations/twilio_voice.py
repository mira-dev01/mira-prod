"""Twilio Voice -- an entirely separate integration from twilio_client.py's
WhatsApp client and from Exotel telephony (exotel_client.py,
app/api/v1/voice.py's exotel_voice_ws, app/voice/pipeline.py's
run_voice_pipeline). Added purely so real-call testing can continue on
Twilio's free trial when Exotel credits run out, without touching any
Exotel code path at all -- see app/config.py's twilio_voice_webhook_token
comment for the full reasoning.

No twilio SDK dependency, matching this codebase's existing style (see
twilio_client.py's own docstring) -- TwiML here is simple enough to build as
a plain string.
"""

from xml.sax.saxutils import escape

from app.config import settings
from app.utils.webhook_auth import verify_shared_secret_token


def verify_voice_webhook_token(token: str | None) -> bool:
    """Same shared-secret-path-token pattern as exotel_client.verify_webhook_token
    -- simpler than implementing Twilio's own X-Twilio-Signature HMAC scheme,
    and consistent with how this codebase already secures the Exotel
    callback."""
    return verify_shared_secret_token(token, settings.twilio_voice_webhook_token)


def build_connect_stream_twiml(stream_ws_url: str, from_number: str, to_number: str) -> str:
    """TwiML telling Twilio to open a bidirectional Media Stream to our
    websocket for the duration of the call -- the Twilio equivalent of
    Exotel's Voicebot Applet config (see app/api/v1/voice.py's module
    docstring), except Twilio needs this returned per-call from an HTTP
    webhook rather than configured once as a static URL in a console.

    from_number/to_number are passed through as <Parameter> custom stream
    parameters -- unlike Exotel, Twilio's own "start" WebSocket event does
    NOT carry the caller/callee numbers at all (confirmed by reading
    pipecat's parse_telephony_websocket: call_data.from_number/to_number for
    Twilio are populated from these exact customParameters, nothing else).
    Without this, run_voice_pipeline_twilio would have no caller number to
    resolve a property/host with.
    """
    from_attr = escape(from_number, {'"': "&quot;"})
    to_attr = escape(to_number, {'"': "&quot;"})
    url_attr = escape(stream_ws_url, {'"': "&quot;"})
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Connect>"
        f'<Stream url="{url_attr}">'
        f'<Parameter name="from_number" value="{from_attr}"/>'
        f'<Parameter name="to_number" value="{to_attr}"/>'
        "</Stream>"
        "</Connect>"
        "</Response>"
    )


def build_reject_twiml() -> str:
    """No property/host is configured for the dialed Twilio number -- the
    Exotel equivalent (run_voice_pipeline's early-return branch) just closes
    the websocket, but Twilio expects TwiML back from this webhook
    regardless, so this politely declines instead of leaving the caller with
    a dead line."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Say>Sorry, this number is not currently configured. Goodbye.</Say>"
        "<Hangup/>"
        "</Response>"
    )
