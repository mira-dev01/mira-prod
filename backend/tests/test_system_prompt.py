import uuid
from datetime import datetime

from app.models.property import Property
from app.models.user import User
from app.prompts import system_prompt
from app.prompts.system_prompt import (
    DEFAULT_ESCALATION_PHRASE,
    build_lead_system_prompt,
    build_system_prompt,
    first_message_for,
    lead_first_message_for,
)


class _FixedDatetime(datetime):
    """Patches system_prompt's `datetime.now(IST)` call to a fixed instant,
    so weekend-date tests don't depend on what day it happens to be run."""

    _fixed = datetime(2026, 6, 30, 19, 0, tzinfo=system_prompt.IST)  # a Tuesday

    @classmethod
    def now(cls, tz=None):
        return cls._fixed


def _user(**overrides) -> User:
    defaults = dict(id=uuid.uuid4(), email="host@example.com", hashed_password="x", name="Asha")
    defaults.update(overrides)
    return User(**defaults)


def _property(**overrides) -> Property:
    defaults = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Glasshouse Studio",
        city="Goa",
        base_price=4500,
        max_guests=4,
    )
    defaults.update(overrides)
    return Property(**defaults)


def test_first_message_default_has_no_placeholders_left_unresolved():
    host = _user(agent_first_message=None)
    msg = first_message_for(_property(), None, host)
    assert "Glasshouse Studio" in msg
    assert "{" not in msg


def test_today_anchor_precomputes_correct_weekend_dates(monkeypatch):
    # Regression: a fast/low-reasoning-effort model was getting weekday/date
    # arithmetic wrong on its own (e.g. calling July 6, 2026 a "Friday" when
    # it's actually a Monday), leading to genuinely wrong booking dates.
    # Pre-computing "this weekend" in code removes that failure mode --
    # the model only has to copy a date, never calculate one. Fixed clock:
    # Tuesday, 2026-06-30, so the upcoming weekend is July 4-5.
    monkeypatch.setattr(system_prompt, "datetime", _FixedDatetime)
    prompt = build_system_prompt(_property(), None, _user())
    assert "2026-07-04 (Saturday) to 2026-07-05 (Sunday)" in prompt
    assert "2026-07-11 to 2026-07-12" in prompt
    assert "Tomorrow is 2026-07-01" in prompt


def test_first_message_uses_host_template_with_placeholders():
    host = _user(name="Asha", agent_first_message="Hi from {host_name} at {property_name} in {city}!")
    msg = first_message_for(_property(name="Sea View Villa", city="Goa"), None, host)
    assert msg == "Hi from Asha at Sea View Villa in Goa!"


def test_first_message_template_blank_on_missing_property_context():
    # Lead Agent first message has no property in scope -- {property_name}
    # must resolve to "" rather than crashing the call.
    host = _user(name="Asha", agent_first_message="Hi from {host_name}, regarding {property_name}!")
    msg = lead_first_message_for(host)
    assert msg == "Hi from Asha, regarding !"


def test_first_message_template_malformed_brace_fails_open_to_literal_text():
    host = _user(agent_first_message="Hello { unterminated")
    msg = first_message_for(_property(), None, host)
    assert msg == "Hello { unterminated"


def test_default_escalation_phrase_used_when_host_has_not_set_one():
    host = _user(agent_escalation_phrase=None)
    prompt = build_system_prompt(_property(), None, host)
    assert DEFAULT_ESCALATION_PHRASE in prompt


def test_host_escalation_phrase_overrides_default():
    host = _user(agent_escalation_phrase="One moment, let me get my colleague Raj.")
    prompt = build_system_prompt(_property(), None, host)
    assert "One moment, let me get my colleague Raj." in prompt
    assert DEFAULT_ESCALATION_PHRASE not in prompt


def test_persona_note_included_when_set():
    host = _user(agent_persona="Sound like a chatty Goan local, very informal.")
    prompt = build_system_prompt(_property(), None, host)
    assert "Sound like a chatty Goan local, very informal." in prompt


def test_persona_note_omitted_when_not_set():
    host = _user(agent_persona=None)
    prompt = build_system_prompt(_property(), None, host)
    assert "Host-defined personality note" not in prompt


def test_property_usp_included_in_guest_support_prompt():
    host = _user()
    prop = _property(usp="Glass house, 1BHK with a private jacuzzi")
    prompt = build_system_prompt(prop, None, host)
    assert "Glass house, 1BHK with a private jacuzzi" in prompt


def test_property_neighborhood_info_included_in_guest_support_prompt():
    host = _user()
    prop = _property(neighborhood_info="10 min walk to Baga beach. Cabs to the airport run ~₹800.")
    prompt = build_system_prompt(prop, None, host)
    assert "10 min walk to Baga beach. Cabs to the airport run ~₹800." in prompt


def test_property_neighborhood_info_omitted_when_not_set():
    host = _user()
    prompt = build_system_prompt(_property(neighborhood_info=None), None, host)
    assert "Neighborhood / local area info" not in prompt


def test_property_usp_omitted_from_lead_agent_portfolio_listing():
    # Same rationale as amenities below: the portfolio listing is resent on
    # every turn of every call, so the USP blurb is deliberately omitted to
    # save tokens (Groq free-tier TPM limit). recommend_properties surfaces
    # the USP for shortlisted properties, so nothing is lost for the booking
    # flow -- see the comment in build_lead_system_prompt.
    host = _user()
    prop = _property(usp="Glass house, 1BHK with a private jacuzzi")
    prompt = build_lead_system_prompt(host, [prop])
    assert "Glasshouse Studio" in prompt
    assert "Glass house, 1BHK with a private jacuzzi" not in prompt


def test_property_amenities_omitted_from_lead_agent_portfolio_listing():
    # Regression: this listing is resent on every turn of every call, for
    # every property in the portfolio -- amenities were a real contributor
    # to hitting Groq's free-tier tokens-per-minute limit mid-call.
    # recommend_properties already surfaces amenities for shortlisted
    # properties, so dropping them here loses no capability.
    host = _user()
    prop = _property(amenities=["WiFi", "Private pool", "EV charger"])
    prompt = build_lead_system_prompt(host, [prop])
    assert "Glasshouse Studio" in prompt
    assert "WiFi" not in prompt
    assert "Private pool" not in prompt


def test_lead_agent_prompt_requires_phone_and_defers_email():
    # Regression: the agent was relying on the guest to volunteer a phone
    # number instead of asking for it, and was asking for email upfront
    # instead of only once a booking is actually being finalized.
    prompt = build_lead_system_prompt(_user(), [])
    assert "Phone number is required for every" in prompt
    assert "email at all unless the guest is finalising a booking" in prompt


def test_lead_agent_prompt_also_gets_persona_and_escalation_overrides():
    host = _user(
        agent_persona="Be extra warm with families.",
        agent_escalation_phrase="Let me loop in the owner directly.",
    )
    prompt = build_lead_system_prompt(host, [])
    assert "Be extra warm with families." in prompt
    assert "Let me loop in the owner directly." in prompt


def test_prompts_warn_against_narrating_internal_workflow_steps():
    # Regression: gpt-oss-120b was observed reciting workflow instructions
    # ("I need to ask for your name, then move to the next question") out
    # loud instead of silently following them.
    host = _user()
    guest_prompt = build_system_prompt(_property(), None, host)
    lead_prompt = build_lead_system_prompt(host, [])
    assert "the guest must never hear any of it" in guest_prompt
    assert "the guest must never hear any of it" in lead_prompt


def test_prompts_instruct_faithful_occasion_capture_without_host_suggestions():
    host = _user()
    guest_prompt = build_system_prompt(_property(), None, host)
    lead_prompt = build_lead_system_prompt(host, [])
    for prompt in (guest_prompt, lead_prompt):
        assert "special occasion" in prompt
        assert "conversation_summary" in prompt
        assert "don't generate ideas for them" in prompt


def test_prompts_handle_ota_discount_comparisons_via_existing_pricing_tools():
    host = _user()
    guest_prompt = build_system_prompt(_property(), None, host)
    lead_prompt = build_lead_system_prompt(host, [])
    for prompt in (guest_prompt, lead_prompt):
        assert "Booking.com" in prompt
        assert "Aur discount milega?" in prompt
        assert "negotiate_rate" in prompt


def test_prompts_allow_sparing_fillers_but_never_after_an_interruption():
    host = _user()
    guest_prompt = build_system_prompt(_property(), None, host)
    lead_prompt = build_lead_system_prompt(host, [])
    for prompt in (guest_prompt, lead_prompt):
        # The interruption-handling ban must still be present, unchanged.
        assert 'Treat "Sure, I\'m here to help" as a banned phrase entirely.' in prompt
        # The new sparing-filler allowance must not undo that ban.
        assert "you may occasionally begin a reply with a short, natural filler word" in prompt
        assert "a filler as a substitute for actually answering" in prompt
