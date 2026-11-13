"""WhatsApp message templates: WHERE TO PASTE EACH TEMPLATE'S SID, and the one function that sends them.

Why templates at all: WhatsApp only delivers a free-text message from a business to someone who has messaged
that business in the last 24 hours (Twilio error 63016 otherwise). A guest who has just PHONED has not, so
every message Mira sends after a call (payment link, receipt, photos, ...) must go as an approved template.

HOW TO USE THIS FILE
  1. In the Twilio console (Messaging -> Content Template Builder) create one template per entry in
     TEMPLATES below. Copy its `body` EXACTLY (including the {{1}}, {{2}} ... placeholders and line breaks),
     choose category "Utility", language English, and submit it for WhatsApp approval.
  2. When Twilio shows the template's Content SID (it starts with "HX"), paste it into the matching
     constant in the block marked "PASTE THE SIDs HERE" just below.
  3. Redeploy. Nothing else to configure. A template whose SID is still "" is simply not used: that message
     goes out as free text instead (works only inside the 24-hour window) and, if WhatsApp refuses it, the
     host gets a ticket to send it by hand -- the same behaviour as before templates existed.

Run `python -m app.services.whatsapp_templates` to print every template (name, category, body, what each
{{n}} must hold) ready to copy into the Twilio console.

RULES THE TEMPLATE BODIES FOLLOW (WhatsApp rejects a template that breaks them): a body never starts or ends
with a {{n}}; placeholders are numbered {{1}}, {{2}} ... without gaps; and a VALUE sent for a placeholder is a
single line (no line breaks, tabs or runs of 4+ spaces) -- `clean_value` below guarantees that.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass


logger = logging.getLogger(__name__)

# =====================================================================================================
#  PASTE THE SIDs HERE  (one per template; leave "" until the template is approved)
#  Each starts with "HX" and is shown next to the template in the Twilio console.
# =====================================================================================================

# --- SPACEZ ONLY: the booking flow -------------------------------------------------------------------
SPACEZ_PAYMENT_LINK_SID = "HXb5cb14f9d85feb9c122a0912eec7606a"          # sent the moment the guest agrees to book: the bill and the payment link
SPACEZ_BOOKING_CONFIRMED_SID = "HX99cec2c7017564549aec73565e025719"     # sent once the payment is received (stay villa): details + stay code
SPACEZ_SHOOT_HELD_SID = "HX68d73026d5d58cf4306b9372f427447a"         # sent once the payment is received (a shoot): "your dates are held"
SPACEZ_PAYMENT_UNSUCCESSFUL_SID = "HXb4ae3182f8cd8f5f77ea8533acd769df"  # sent when the guest's payment failed / the checkout was closed: try again
SPACEZ_STAY_FEE_LINK_SID = "HXe29e0e8b5077e31b736995c7ecd17a6d"         # early check-in / late check-out approved by the team: the fee's payment link
SPACEZ_STAY_REQUEST_DECLINED_SID = "HX7ef197fc70d7bddf8debf66f2b3457cd" # early check-in / late check-out declined by the team
SPACEZ_STAY_FEE_RECEIVED_SID = "HXad5f915415854a0e3bd220d23a737ee2"     # early check-in / late check-out fee received

# --- ALL HOSTS: photos and property lists ----------------------------------------------------------
PHOTOS_SID = "HX3609a3fad76886c626e83f24b9f98428"                       # photos of ONE property (a link to its gallery page)
SHORTLIST_SID = "HXdb9a01d8c2065aa36e5d19fc9cfbe510"                    # the list of every property recommended on the call + one link with their photos

# --- ALL HOSTS: busy line + host alerts ------------------------------------------------------------
# These four are not in TEMPLATES below: their bodies live in scripts/create_*_template.py (two have a
# dashboard button) and each caller builds its own plain-text fallback. "" = that plain-text fallback.
BUSY_RECOVERY_SID = "HX02e0139a6bb81225ed92d0b3efea7b74"   # mira_busy_recovery: guest's call was rejected as busy -> numbered menu
AVAILABILITY_SID = "HXc49915f7f771c6f32142739573901deb"    # mira_availability: that guest, once the line is free again
ESCALATION_SID = "HXdf0bc43de0842a2de1745345d714a1c7"      # mira_escalation: host alert (escalation / missed call), "Go to Dashboard" button
GUEST_CALLING_SID = ""                                     # mira_guest_calling: host alert "a guest is calling Mira now" + Take Call link


@dataclass(frozen=True)
class Template:
    key: str
    sid_const: str  # the name of the constant above that holds this template's SID
    purpose: str
    body: str  # EXACTLY what to create in the Twilio console
    variables: tuple[str, ...]  # what {{1}}, {{2}} ... hold, in order

    @property
    def sid(self) -> str:
        """The SID pasted above ("" = not set up yet). Read at send time, so a change takes effect at once."""
        return str(globals().get(self.sid_const) or "").strip()


_FOOTER = "Regards,\nTeam Spacez\n\nwww.spacez.co"

TEMPLATES: dict[str, Template] = {
    t.key: t
    for t in (
        # -- Spacez: booking ----------------------------------------------------------------------
        Template(
            "spacez_payment_link",
            "SPACEZ_PAYMENT_LINK_SID",
            "Payment link with the calculation of the grand total",
            "Hi {{1}}, complete your Spacez booking payment here: {{2}}\n\n"
            "Your booking: {{3}}\n"
            "Bill: {{4}}\n"
            "Grand total: {{5}}\n\n"
            "Your dates are held while you pay.\n\n"
            "Team Spacez",
            (
                "guest's first name",
                "the payment link (https://...)",
                "villa, dates and party, e.g. Deccan Escape, 12 Dec 2026 to 14 Dec 2026, 8 adults",
                "the calculation on ONE line, e.g. Stay Rs 14,000 + Extra guests Rs 1,500 = Rs 15,500, + GST (5%) Rs 775",
                "what to pay, e.g. Rs 16,275 (full payment) or Rs 8,138 now, Rs 8,137 due by 5 Dec 2026",
            ),
        ),
        Template(
            "spacez_booking_confirmed",
            "SPACEZ_BOOKING_CONFIRMED_SID",
            "Payment received: the booking details (the document's confirmation message) and the stay code",
            "Hello {{1}}, Your Booking is now confirmed\n"
            "The Booking Details is following:\n"
            "Booking ID: {{2}}\n"
            "Stay Name: {{3}}\n"
            "Stay Selection: Full villa\n"
            "Check in Date: {{4}}\n"
            "Check Out Date: {{5}}\n"
            "Number of Guests: {{6}}\n"
            "Breakfast Purchased: {{7}}\n"
            "Lunch purchased: {{8}}\n"
            "Dinner purchased: {{9}}\n"
            "Grill kit: {{10}}\n"
            "Food Platter: {{11}}\n"
            "Movie kit: {{12}}\n"
            "Floating kit: {{13}}\n"
            "Stay Purpose: {{14}}\n"
            "Payment Amount: {{15}}\n"
            "Payment Remaining: {{16}}\n"
            "Payment Status: {{17}}\n"
            "Booking Status: Pending Host Approval\n"
            "Your stay code: {{18}}\n\n"
            "Spacez Food and Hospitality takes pleasure in promptly satisfying our guest's stay needs. "
            "Please leave a text here for any concern or requests.\n\n" + _FOOTER,
            (
                "guest's first name",
                "booking id, e.g. SPZ-1A2B3C",
                "villa name",
                "check-in date, e.g. 12 Dec, 2026",
                "check-out date",
                "e.g. 8 Adults, 0 Children, 0 Pets",
                "Yes / No / Free",
                "Yes / No",
                "Yes / No",
                "number of grill kits, e.g. 0",
                "number of food platters, e.g. 0",
                "number of movie kits, e.g. 0",
                "number of floating kits, e.g. 0",
                "stay purpose, e.g. Birthday",
                "amount paid now, e.g. ₹8,138",
                "amount still to pay, e.g. ₹8,137",
                "Paid in full / Half paid",
                "the stay code, e.g. DECC-0007",
            ),
        ),
        Template(
            "spacez_shoot_held",
            "SPACEZ_SHOOT_HELD_SID",
            "Payment received for a shoot: dates held, the team will call",
            "Hello {{1}}, Payment successful. Your dates are held.\n\n"
            "Booking ID: {{2}}\n"
            "Stay Name: {{3}}\n"
            "Check in Date: {{4}}\n"
            "Check Out Date: {{5}}\n"
            "Your stay code: {{6}}\n\n"
            "Our team will call you to understand the crew and setup, then confirm your booking.\n\n" + _FOOTER,
            (
                "guest's first name",
                "booking id",
                "villa name",
                "check-in date",
                "check-out date",
                "the stay code",
            ),
        ),
        Template(
            "spacez_payment_unsuccessful",
            "SPACEZ_PAYMENT_UNSUCCESSFUL_SID",
            "Payment failed or checkout closed: try again with the same link",
            "Hi {{1}}, Payment unsuccessful. Almost there! Your villa is held, just one more try needed "
            "to finish booking your stay.\n\n"
            "Try again here: {{2}}\n"
            "Your dates stay held until {{3}}.\n\n"
            "Team Spacez",
            (
                "guest's first name",
                "the same payment link",
                "when the hold ends, e.g. 4:30 PM IST on 5 Dec",
            ),
        ),
        Template(
            "spacez_stay_fee_link",
            "SPACEZ_STAY_FEE_LINK_SID",
            "Early check-in / late check-out approved: pay the flat fee",
            "Hi {{1}}! Good news: our team can arrange {{2}} for your stay at {{3}} ({{4}}).\n\n"
            "The flat charge is {{5}}. Please pay here to confirm it: {{6}}\n\n" + _FOOTER,
            (
                "guest's first name",
                "what was asked, e.g. early check-in and late check-out",
                "villa name",
                "stay dates, e.g. 12 Dec 2026 to 14 Dec 2026",
                "the fee, e.g. Rs 1,500",
                "the payment link",
            ),
        ),
        Template(
            "spacez_stay_request_declined",
            "SPACEZ_STAY_REQUEST_DECLINED_SID",
            "Early check-in / late check-out declined",
            "Hi {{1}}, thank you for asking. Our team won't be able to arrange {{2}} for your stay at {{3}} ({{4}}). "
            "Everything else about your booking stays exactly as it is. "
            "Please message us here if there is anything else we can do.\n\n" + _FOOTER,
            (
                "guest's first name",
                "what was asked",
                "villa name",
                "stay dates",
            ),
        ),
        Template(
            "spacez_stay_fee_received",
            "SPACEZ_STAY_FEE_RECEIVED_SID",
            "Early check-in / late check-out fee received",
            "Thank you {{1}}! We've received {{2}} for {{3}} at {{4}} ({{5}}). "
            "Our team will look after the timing on the day.\n\n" + _FOOTER,
            (
                "guest's first name",
                "the amount, e.g. Rs 1,500",
                "what was paid for",
                "villa name",
                "stay dates",
            ),
        ),
        # -- all hosts: photos and lists -------------------------------------------------------
        Template(
            "photos",
            "PHOTOS_SID",
            "Photos of one property (a gallery link)",
            "Hi {{1}}, here are the photos of {{2}} you asked for: {{3}}\n\n"
            "Reply here if you would like to know anything else.\n\n"
            "Sent by {{4}} via Mira.",
            (
                "guest's first name",
                "property name",
                "the gallery link",
                "the host's business name",
            ),
        ),
        Template(
            "shortlist",
            "SHORTLIST_SID",
            "Every property recommended on the call, with one link to see them all with photos",
            "Hi {{1}}, here are the stays we talked about: {{2}}.\n\n"
            "See the details and photos of each one here: {{3}}\n\n"
            "Reply here if you would like to know anything else.\n\n"
            "Sent by {{4}} via Mira.",
            (
                "guest's first name",
                "the properties on ONE line, e.g. 1) Villa Aura, Goa 2) Hill Haven, Manali",
                "the link to the list page (all properties with photos)",
                "the host's business name",
            ),
        ),
    )
}

_WS = re.compile(r"\s+")
MAX_VALUE_CHARS = 600


def clean_value(value) -> str:
    """One line a template placeholder will accept: line breaks, tabs and runs of spaces collapse to one
    space, it is never empty (WhatsApp refuses an empty value) and never longer than MAX_VALUE_CHARS."""
    text = _WS.sub(" ", str(value if value is not None else "")).strip()
    return (text[: MAX_VALUE_CHARS - 1].rstrip() + "…") if len(text) > MAX_VALUE_CHARS else (text or "-")


async def send(to_phone: str, key: str, values: list, *, fallback_text: str) -> str | None:
    """Sends the template `key` to a guest. `values` fill {{1}}, {{2}} ... in order. Returns the message sid
    once WhatsApp accepted it, or None when it did not (the caller then tells the host).

      * SID set  -> the approved template (works for any guest, any time).
      * SID ""   -> `fallback_text` as free text (only reaches a guest inside the 24-hour window).
      * the template itself failing (not approved yet, wrong variable count) -> one free-text try with
        `fallback_text`, and the failure is logged so it is noticed.

    Never raises: a messaging problem must not break a live call or the payment flow around it."""
    from app.integrations import twilio_client  # here, so printing the catalog needs no server settings

    template = TEMPLATES[key]
    if template.sid:
        expected = len(template.variables)
        if len(values) != expected:
            logger.error("whatsapp template %s needs %d values, got %d", key, expected, len(values))
        else:
            try:
                result = await twilio_client.send_whatsapp_template(
                    to_phone, template.sid, {str(i): clean_value(v) for i, v in enumerate(values, start=1)}
                )
                if result.get("status") == "skipped":
                    logger.info("WhatsApp template %s to %s skipped: %s", key, to_phone, result.get("reason"))
                    return None
                if sid := result.get("sid"):
                    return sid
            except Exception:
                logger.exception("whatsapp template %s was not accepted for %s; trying free text", key, to_phone)
    return await twilio_client.send_whatsapp_tracked(to_phone, fallback_text)


def render(key: str, values: list) -> str:
    """The template's text with the values filled in. This is also the free-text fallback, so a guest sees the
    same message whichever way it reaches them."""
    text = TEMPLATES[key].body
    for i, value in enumerate(values, start=1):
        text = text.replace("{{%d}}" % i, clean_value(value))
    return text


def describe() -> str:
    """Every template as text, for copying into the Twilio console."""
    out = []
    for template in TEMPLATES.values():
        out.append(f"=== {template.key}  [{'SID set' if template.sid else 'SID NOT SET'}]  Utility, English ===")
        out.append(f"Used for: {template.purpose}")
        out.append("Body (copy exactly):")
        out.append(template.body)
        out.append("Placeholders:")
        out.extend(f"  {{{{{i}}}}} = {meaning}" for i, meaning in enumerate(template.variables, start=1))
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    print(describe())
