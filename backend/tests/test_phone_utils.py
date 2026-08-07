"""Staff-engineer review finding: email_templates._whatsapp_link and
twilio_client._to_whatsapp_address independently re-implemented the same
India-phone WhatsApp-digit rule -- consolidated into
app.utils.phone.to_india_whatsapp_digits. These tests pin the shared
behavior both call sites now depend on."""

from app.utils.phone import to_india_whatsapp_digits


def test_bare_ten_digit_number_gets_91_prefix():
    assert to_india_whatsapp_digits("9876543210") == "919876543210"


def test_already_91_prefixed_number_is_untouched():
    assert to_india_whatsapp_digits("919876543210") == "919876543210"


def test_plus_and_spaces_are_stripped():
    assert to_india_whatsapp_digits("+91 98765 43210") == "919876543210"


def test_empty_string_returns_empty_string():
    assert to_india_whatsapp_digits("") == ""


def test_non_ten_digit_non_91_number_is_left_as_is():
    # Not a recognized shape -- passed through rather than guessed at.
    assert to_india_whatsapp_digits("12345") == "12345"
