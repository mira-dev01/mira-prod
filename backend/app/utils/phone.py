import re


def to_india_whatsapp_digits(phone: str) -> str:
    """Bare digit string, country-code-prefixed, for WhatsApp addressing.

    Exotel numbers are stored/spoken in Indian local formats; both wa.me
    links and Twilio's whatsapp: prefix need a bare 91-prefixed digit
    string with no +/spaces/punctuation. Defaults to +91 when no country
    code is present rather than rejecting, since MIRA is India-only today.
    """
    digits = re.sub(r"\D", "", phone)
    if digits and not digits.startswith("91") and len(digits) == 10:
        digits = "91" + digits
    return digits
