import uuid
from datetime import date, datetime

from app.models.guest_profile import GuestProfile
from app.models.lead import Lead
from app.models.property import Property
from app.models.user import User
from app.prompts import system_prompt
from app.prompts.system_prompt import (
    DEFAULT_ESCALATION_PHRASE,
    _active_booking_section,
    _active_seasonal_notes,
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


def _guest(**overrides) -> GuestProfile:
    defaults = dict(id=uuid.uuid4(), phone="+919999999999", total_stays=0)
    defaults.update(overrides)
    return GuestProfile(**defaults)


def _booking(**overrides) -> Lead:
    defaults = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        status="booked",
        guest_name="Priya",
        properties_discussed=["Alpine Ridge Chalet"],
        check_in=date(2026, 8, 1),
        check_out=date(2026, 8, 3),
    )
    defaults.update(overrides)
    return Lead(**defaults)


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
    # Regression: "this weekend" and "next weekend" were previously (wrongly)
    # documented as the same dates -- they must resolve to different weekends.
    assert "\"This weekend\" means the upcoming 2026-07-04 (Saturday) to 2026-07-05 (Sunday)" in prompt
    assert "\"Next weekend\" means the weekend AFTER that: 2026-07-11 (Saturday) to 2026-07-12 (Sunday)" in prompt
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


def test_negotiation_off_note_omitted_by_default():
    """Regression test: _user()'s in-memory User never went through a DB
    flush, so negotiation_allowed is None (server_default only applies on
    INSERT) -- this must be treated as "unset"/allowed, not "disabled",
    otherwise every host who hasn't touched this setting would silently get
    told negotiation is off."""
    host = _user()
    assert host.negotiation_allowed is None  # confirms the in-memory default this test guards against
    prompt = build_system_prompt(_property(), None, host)
    assert "does not offer discounts" not in prompt


def test_negotiation_off_note_included_when_explicitly_disabled():
    host = _user(negotiation_allowed=False)
    prompt = build_system_prompt(_property(), None, host)
    assert "does not offer discounts" in prompt


def test_no_guest_profile_says_new_guest():
    prompt = build_system_prompt(_property(), None, _user())
    assert "not in our guest records" in prompt
    assert "returning guest" not in prompt


def test_guest_profile_with_zero_stays_is_treated_as_new():
    """A GuestProfile row that was JUST created for this call (total_stays
    still 0, per call_service.get_or_create_guest_profile) is a first-time
    caller, not a returning one."""
    guest = _guest(total_stays=0)
    prompt = build_system_prompt(_property(), guest, _user())
    assert "not in our guest records" in prompt
    assert "returning guest" not in prompt


def test_returning_guest_included_with_name_and_stay_count():
    guest = _guest(name="Priya", total_stays=3)
    prompt = build_system_prompt(_property(), guest, _user())
    assert "returning guest: Priya, 3 past stay(s)" in prompt


def test_returning_guest_includes_preferred_language_and_last_outcome():
    guest = _guest(name="Priya", total_stays=2, preferred_language="Hinglish", last_outcome="hot")
    prompt = build_system_prompt(_property(), guest, _user())
    assert "Prefers Hinglish." in prompt
    assert "Last call ended: hot." in prompt


def test_returning_guest_includes_last_conversation_summary():
    guest = _guest(
        name="Priya",
        total_stays=2,
        conversation_summaries=[
            {"summary": "Asked about parking at Villa Sunset, budget-conscious.", "lead_temperature": "warm"}
        ],
    )
    prompt = build_system_prompt(_property(), guest, _user())
    assert "Asked about parking at Villa Sunset, budget-conscious." in prompt


def test_lead_agent_prompt_also_gets_guest_memory_section():
    guest = _guest(name="Priya", total_stays=1)
    prompt = build_lead_system_prompt(_user(), [], guest)
    assert "returning guest: Priya, 1 past stay(s)" in prompt


def test_lead_agent_prompt_defaults_to_new_guest_when_omitted():
    """Every existing call site that doesn't pass guest= must keep working
    exactly as before -- confirms the new parameter is genuinely optional."""
    prompt = build_lead_system_prompt(_user(), [])
    assert "not in our guest records" in prompt


def test_negotiation_off_note_omitted_when_explicitly_enabled():
    host = _user(negotiation_allowed=True)
    prompt = build_system_prompt(_property(), None, host)
    assert "does not offer discounts" not in prompt


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


# --- Property Memory: seasonal notes (memory-architecture-plan.md section 5) ---


def test_active_seasonal_notes_simple_range():
    notes = [{"note": "Pool closed for maintenance.", "start_month": 6, "end_month": 8}]
    assert _active_seasonal_notes(notes, today=date(2026, 7, 1)) == ["Pool closed for maintenance."]
    assert _active_seasonal_notes(notes, today=date(2026, 9, 1)) == []


def test_active_seasonal_notes_wraparound_range():
    """Nov-Feb (11-2) is a valid wraparound range spanning the year
    boundary -- must be active in Nov/Dec/Jan/Feb, inactive in Mar-Oct."""
    notes = [{"note": "Extra heater provided.", "start_month": 11, "end_month": 2}]
    assert _active_seasonal_notes(notes, today=date(2026, 12, 15)) == ["Extra heater provided."]
    assert _active_seasonal_notes(notes, today=date(2026, 1, 15)) == ["Extra heater provided."]
    assert _active_seasonal_notes(notes, today=date(2026, 2, 28)) == ["Extra heater provided."]
    assert _active_seasonal_notes(notes, today=date(2026, 11, 1)) == ["Extra heater provided."]
    assert _active_seasonal_notes(notes, today=date(2026, 6, 15)) == []
    assert _active_seasonal_notes(notes, today=date(2026, 3, 1)) == []


def test_active_seasonal_notes_handles_none_and_empty():
    assert _active_seasonal_notes(None, today=date(2026, 6, 1)) == []
    assert _active_seasonal_notes([], today=date(2026, 6, 1)) == []


def test_active_seasonal_notes_skips_malformed_entries():
    notes = [{"note": "Missing months"}, {"start_month": 1, "end_month": 2}]  # no "note" key
    assert _active_seasonal_notes(notes, today=date(2026, 1, 15)) == []


def test_active_seasonal_notes_multiple_notes_only_active_ones_returned():
    notes = [
        {"note": "Pool closed in monsoon.", "start_month": 6, "end_month": 8},
        {"note": "Extra heater Nov-Feb.", "start_month": 11, "end_month": 2},
    ]
    assert _active_seasonal_notes(notes, today=date(2026, 7, 1)) == ["Pool closed in monsoon."]
    assert _active_seasonal_notes(notes, today=date(2026, 1, 1)) == ["Extra heater Nov-Feb."]
    assert _active_seasonal_notes(notes, today=date(2026, 4, 1)) == []


def test_build_system_prompt_includes_active_seasonal_note(monkeypatch):
    monkeypatch.setattr(system_prompt, "datetime", _FixedDatetime)  # fixed at 2026-06-30
    prop = _property(seasonal_notes=[{"note": "Pool closed for monsoon cleaning.", "start_month": 6, "end_month": 8}])
    prompt = build_system_prompt(prop, None, _user())
    assert "Pool closed for monsoon cleaning." in prompt
    assert "Seasonal notes currently in effect" in prompt


def test_build_system_prompt_omits_inactive_seasonal_note(monkeypatch):
    monkeypatch.setattr(system_prompt, "datetime", _FixedDatetime)  # fixed at 2026-06-30
    prop = _property(seasonal_notes=[{"note": "Extra heater provided.", "start_month": 11, "end_month": 2}])
    prompt = build_system_prompt(prop, None, _user())
    assert "Extra heater provided." not in prompt
    assert "Seasonal notes currently in effect" not in prompt


# --- Active booking recognition (Lead.status == "booked") ---


def test_active_booking_section_empty_when_no_booking():
    assert _active_booking_section(None) == ""


def test_active_booking_section_includes_property_dates_and_name():
    booking = _booking()
    section = _active_booking_section(booking)
    assert "Alpine Ridge Chalet" in section
    assert "2026-08-01 to 2026-08-03" in section
    assert "Priya" in section
    assert "confirmed booking" in section


def test_active_booking_section_handles_missing_dates():
    booking = _booking(check_in=None, check_out=None)
    section = _active_booking_section(booking)
    assert "dates not yet on file" in section


def test_active_booking_section_handles_missing_guest_name():
    booking = _booking(guest_name=None)
    section = _active_booking_section(booking)
    assert "name not on file" in section


def test_active_booking_section_uses_last_discussed_property():
    booking = _booking(properties_discussed=["Sea View Villa", "Alpine Ridge Chalet"])
    section = _active_booking_section(booking)
    assert "Alpine Ridge Chalet" in section


def test_build_system_prompt_includes_active_booking_when_present():
    prompt = build_system_prompt(_property(), None, _user(), active_booking=_booking())
    assert "confirmed booking" in prompt
    assert "Alpine Ridge Chalet" in prompt


def test_build_system_prompt_omits_booking_section_when_none():
    prompt = build_system_prompt(_property(), None, _user())
    assert "confirmed booking" not in prompt


def test_build_lead_system_prompt_includes_active_booking_when_present():
    prompt = build_lead_system_prompt(_user(), [], active_booking=_booking())
    assert "confirmed booking" in prompt
    assert "Alpine Ridge Chalet" in prompt


def test_build_system_prompt_handles_no_seasonal_notes():
    """seasonal_notes is None for an in-memory Property never flushed
    through the DB (server_default only applies on INSERT) -- must not
    error, same pattern as the negotiation_allowed/total_stays None-handling
    bugs found elsewhere in this file."""
    prop = _property()
    assert prop.seasonal_notes is None
    prompt = build_system_prompt(prop, None, _user())
    assert "Seasonal notes currently in effect" not in prompt


def test_golden_rules_forbid_inventing_tool_call_arguments():
    # Regression: Mira called check_calendar with a check_in/check_out/
    # num_guests the guest never gave (confirmed live, 2026-07-21), then
    # told the guest the property was "available" for those invented dates.
    prompt = build_system_prompt(_property(), None, _user())
    assert "never invent a plausible-sounding placeholder date or number" in prompt


def test_golden_rules_forbid_narrator_meta_text():
    # Regression: "---This is the end.---" got spoken directly to a guest,
    # appended after a real sentence (confirmed live, 2026-07-21).
    prompt = build_system_prompt(_property(), None, _user())
    assert "This is the end." in prompt
    assert "stage direction" in prompt


def test_golden_rules_hello_mid_call_never_repeats_last_answer():
    # Regression: a guest said "Hello" mid-call and got a near-verbatim
    # repeat of the full attractions list Mira had just given, instead of a
    # brief "I'm here" (confirmed live, 2026-07-21).
    prompt = build_system_prompt(_property(), None, _user())
    assert "not a request to hear your last answer again" in prompt
