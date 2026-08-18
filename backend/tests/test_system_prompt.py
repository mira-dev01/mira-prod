import uuid
from datetime import date, datetime

from app.models.faq_entry import FaqEntry
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


def test_today_anchor_named_month_example_demonstrates_the_rollover_it_teaches(monkeypatch):
    """Intelligent slot collection ("Phase 3"): "first weekend of October"
    names a month rather than being relative to today, so this/next
    weekend's pre-computed dates don't cover it -- confirmed via grep that
    no month-name date pattern existed before this change.

    The worked example is deliberately a month EARLIER in the calendar than
    today's month, resolved to NEXT year -- self-review caught that picking
    the NEXT calendar month (the first version of this fix) would almost
    always land in the current year and never actually demonstrate the
    "already passed this year -> means next year" rule stated right next to
    it, for 11 of 12 months a real call could be placed in. An earlier
    month always needs the rollover (a booking can't resolve into the
    past), so the example now genuinely matches the rule it's teaching.
    Fixed clock: Tuesday, 2026-06-30 -- May (the month before June) has
    already passed this year, so it means May 2027, whose first Saturday is
    2027-05-01."""
    monkeypatch.setattr(system_prompt, "datetime", _FixedDatetime)
    prompt = build_system_prompt(_property(), None, _user())
    assert "the first weekend of a month is its first saturday" in prompt.lower()
    assert "first weekend of May 2027 is 2027-05-01 (Saturday) to 2027-05-02 (Sunday)" in prompt


def test_today_anchor_january_has_no_earlier_month_this_year_to_roll_over(monkeypatch):
    """January is the one month with no earlier month within the same
    year to demonstrate the rollover rule with -- falls back to December
    of THIS year instead (still a real, useful, forward-looking example;
    just one that doesn't happen to need the rollover branch). Confirms
    this fallback lands on a sensible date rather than erroring or
    resolving into the past."""

    class _JanuaryFixedDatetime(datetime):
        _fixed = datetime(2026, 1, 15, 19, 0, tzinfo=system_prompt.IST)

        @classmethod
        def now(cls, tz=None):
            return cls._fixed

    monkeypatch.setattr(system_prompt, "datetime", _JanuaryFixedDatetime)
    prompt = build_system_prompt(_property(), None, _user())
    assert "first weekend of December is 2026-12-05 (Saturday) to 2026-12-06 (Sunday)" in prompt
    # Same-year example: the year must NOT be spelled out (would read as
    # oddly formal for a guest asking about a month later this same year).
    assert "December 2026" not in prompt


def test_first_message_uses_host_template_with_placeholders():
    host = _user(name="Asha", agent_first_message="Hi from {host_name} at {property_name} in {city}!")
    msg = first_message_for(_property(name="Sea View Villa", city="Goa"), None, host)
    assert msg == "Hi from Asha at Sea View Villa in Goa!"


def test_first_message_prefers_spoken_name_over_raw_name():
    # spoken_name is what should actually be said aloud -- never the raw
    # scraped Airbnb marketing title, even when display_name/spoken_name
    # exist alongside a messier `name`.
    host = _user(agent_first_message=None)
    property_ = _property(name="Pine - Glasshouse Suite w/bathtub | Pause Project", spoken_name="Pine")
    msg = first_message_for(property_, None, host)
    assert "Pine" in msg
    assert "Glasshouse Suite" not in msg


def test_build_system_prompt_uses_display_name_over_raw_name():
    property_ = _property(name="Pine - Glasshouse Suite w/bathtub | Pause Project", display_name="Pine - Suite w/bathtub")
    prompt = build_system_prompt(property_, None, _user())
    assert "Pine - Suite w/bathtub" in prompt


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


def test_pricing_order_rule_routes_unquantified_pushback_to_negotiate_rate():
    """Phase 4D (Phase 4C/S.1 finding): unquantified pushback ("can you do
    better?", no number named) must route to negotiate_rate, not a second
    get_pricing(apply_discounts=true) call -- confirms the prompt no longer
    tells the model apply_discounts=true is the pushback path."""
    prompt = build_system_prompt(_property(), None, _user())
    normalized = " ".join(prompt.split())  # collapse the prompt's own line-wrapping/indentation
    assert "call negotiate_rate and present the revised price" in normalized
    assert "leave guest_offer unset if they didn't name their own number" in normalized
    # The old instruction routing unnamed pushback to a second get_pricing
    # call must be gone -- this exact phrase is what Phase 4C/S.1 found was
    # the structural gap (unquantified pushback never reaching policy
    # resolution at all).
    assert "call get_pricing again with apply_discounts=true" not in normalized


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


def test_legacy_property_faq_still_inlined():
    """Property.faq (the legacy inline column) must keep working for hosts/
    properties not yet migrated to FaqEntry rows (see alembic 7a7297081aaa)."""
    host = _user()
    prop = _property(faq=[{"question": "Is parking free?", "answer": "Yes, in the driveway."}])
    prompt = build_system_prompt(prop, None, host)
    assert "Q: Is parking free?" in prompt
    assert "A: Yes, in the driveway." in prompt


def test_verified_faq_entries_inlined_alongside_legacy_faq():
    """The per-property FAQ editor now writes structured FaqEntry rows
    instead of the legacy Property.faq column -- they must be inlined into
    the static prompt the same way, not just reachable via the search_faq
    tool, so behavior for hosts using the dashboard FAQ tab matches what the
    legacy column always did."""
    host = _user()
    prop = _property(faq=[{"question": "Is parking free?", "answer": "Yes, in the driveway."}])
    entry = FaqEntry(
        user_id=prop.user_id,
        property_id=prop.id,
        question="Is there a pool?",
        answer="Yes, a private plunge pool.",
        status="verified",
    )
    prompt = build_system_prompt(prop, None, host, verified_faq_entries=[entry])
    assert "Q: Is parking free?\nA: Yes, in the driveway." in prompt
    assert "Q: Is there a pool?\nA: Yes, a private plunge pool." in prompt


def test_faq_section_omitted_when_no_legacy_or_verified_entries():
    host = _user()
    prompt = build_system_prompt(_property(faq=[]), None, host, verified_faq_entries=[])
    assert "Frequently asked questions" not in prompt


def test_verified_faq_entries_defaults_to_none_for_existing_call_sites():
    """Every existing call site that doesn't pass verified_faq_entries= must
    keep working exactly as before."""
    host = _user()
    prop = _property(faq=[{"question": "Is parking free?", "answer": "Yes, in the driveway."}])
    prompt = build_system_prompt(prop, None, host)
    assert "Q: Is parking free?\nA: Yes, in the driveway." in prompt


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


def test_lead_agent_portfolio_listing_uses_display_name_over_raw_name():
    host = _user()
    prop = _property(
        name="Pine - Glasshouse Suite w/bathtub | Pause Project",
        display_name="Pine - Suite w/bathtub",
    )
    prompt = build_lead_system_prompt(host, [prop])
    assert "Pine - Suite w/bathtub" in prompt
    assert "Pine - Glasshouse Suite w/bathtub | Pause Project" not in prompt


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


def test_golden_rules_saturday_minimum_stay_asks_before_declining():
    # A Saturday-only request when Property.saturday_minimum_stay_enabled is
    # on shouldn't be treated as a flat refusal on the first ask -- the
    # policy should be relayed and the guest asked about Saturday+Sunday
    # first, only actually declined if they insist on Saturday alone.
    prompt = build_system_prompt(_property(), None, _user())
    assert "Saturday-minimum-stay policy" in prompt
    assert "don't treat that as a flat" in prompt
    assert "insists on Saturday alone should you tell them" in prompt


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


def test_golden_rules_forbid_loop_in_the_host_phrasing_on_booking_accept():
    # A guest accepting a price and wanting to book should hear a concrete
    # next step (WhatsApp + payment details), not a vague "I'll loop in the
    # host" -- requested 2026-07-21.
    prompt = build_system_prompt(_property(), None, _user())
    assert 'Never say "let me loop in the host"' in prompt
    assert "follow up with you on WhatsApp shortly with" in prompt
    assert "the payment details to confirm your booking" in prompt


def test_golden_rules_forbid_loop_in_the_host_for_any_escalation_reason():
    # Regression, confirmed live 2026-07-21: the original fix only covered
    # the booking-accept moment, but Mira said "One sec, let me loop in the
    # host directly!" right after an escalate_to_host call for a routine
    # support question (an early-check-in ask), not a booking. The rule must
    # ban the phrase for escalate_to_host generally, not just booking.
    prompt = build_system_prompt(_property(), None, _user())
    assert "for ANY reason, not just booking" in prompt
    assert "every time you call escalate_to_host" in prompt


def test_caller_phone_is_exposed_so_the_model_never_needs_it_recited():
    # Feature request 2026-07-21: "just send me the photos on the number I'm
    # calling from" should work without asking the guest to say their
    # number aloud -- the telephony layer already knows it.
    prompt = build_system_prompt(_property(), None, _user(), caller_phone="+919876543210")
    assert "already known from the call itself: +919876543210" in prompt
    assert "never ask them to say or repeat their number aloud" in prompt


def test_caller_phone_section_omitted_when_not_available():
    # Browser test calls have no real caller number -- must not fabricate one.
    prompt = build_system_prompt(_property(), None, _user(), caller_phone=None)
    assert "already known from the call itself" not in prompt


def test_lead_caller_phone_is_exposed_too():
    prompt = build_lead_system_prompt(_user(), [_property()], caller_phone="+919876543210")
    assert "already known from the call itself: +919876543210" in prompt


def test_guest_memory_says_not_to_reask_a_known_returning_guests_name():
    # Regression 2026-07-21: a returning guest whose booking/dates were
    # correctly recalled from Guest Memory was still asked for their phone
    # number again mid-call -- extend the same "don't re-ask" guarantee
    # explicitly to a known name, not just implied by stating it once.
    guest = _guest(name="Deepika", total_stays=2)
    prompt = build_system_prompt(_property(), guest, _user())
    assert "You already know their name is Deepika -- use it naturally, never ask for it again." in prompt


def test_guest_memory_omits_reask_line_when_name_unknown():
    guest = _guest(name=None, total_stays=2)
    prompt = build_system_prompt(_property(), guest, _user())
    assert "never ask for it again" not in prompt


def test_golden_rules_save_unprompted_name_via_update_lead_in_guest_support():
    # Regression, confirmed live 2026-07-21: a guest said "Hi, my name is
    # Deepika" as the very first thing in a Guest Support call (property
    # already known, no lead-qualification workflow), and Mira never called
    # update_lead with it at all -- GUEST_SUPPORT_INSTRUCTIONS had no
    # equivalent of LEAD_AGENT_INSTRUCTIONS' "save name/phone the moment you
    # learn it" step. The dashboard/Guest Memory then had nothing to sync
    # and fell back to a stale name from a much earlier call.
    prompt = build_system_prompt(_property(), None, _user())
    assert "call update_lead immediately with that field" in prompt
    assert "whether you asked for it or they" in prompt
    assert "volunteered it completely unprompted" in prompt


def test_golden_rules_save_unprompted_name_also_present_in_lead_agent_prompt():
    prompt = build_lead_system_prompt(_user(), [_property()])
    assert "call update_lead immediately with that field" in prompt


def test_golden_rules_covers_guest_self_correction():
    """Phase 6.2 audit (documentation/agent-conversation-improvement.md,
    requirement #13 Recovery Behaviour): a guest correcting an earlier value
    ("actually, make that 6 guests") was not explicitly covered by any
    existing rule -- only re-asking (NEVER RE-ASK) and incomplete/filler
    turns were. state.slots' own overwrite mechanics (Phase 1.2) already
    handle this correctly at the data layer; this confirms the PROMPT layer
    actually tells the model to treat a correction as authoritative, not
    just rely on the mechanics being silently correct underneath."""
    prompt = build_system_prompt(_property(), None, _user())
    assert "CORRECTS a value they already gave" in prompt
    assert "re-call any tool whose result depended on the old value" in prompt
    # Shared via GOLDEN_RULES, so present in both modes.
    lead_prompt = build_lead_system_prompt(_user(), [_property()])
    assert "CORRECTS a value they already gave" in lead_prompt


def test_golden_rules_covers_answer_first_then_return_to_flow():
    """Conversation-robustness pass ("answer-first-then-return-to-flow"):
    a guest asking something unrelated mid-flow (mid-availability-check,
    mid-pricing, mid-negotiation) was not explicitly covered by any existing
    rule -- the closest existing clauses were about declining out-of-scope
    topics (a different kind of "topic") and reaction-warmth bridging
    between PLANNED steps, neither of which addresses a genuine guest-
    initiated detour. Shared via GOLDEN_RULES, so present in both modes."""
    prompt = build_system_prompt(_property(), None, _user())
    assert "UNRELATED to what you were just doing" in prompt
    assert "bridge back naturally" in prompt
    lead_prompt = build_lead_system_prompt(_user(), [_property()])
    assert "UNRELATED to what you were just doing" in lead_prompt


def test_golden_rules_covers_conversational_memory_callback_wording():
    """Conversation-robustness pass: distinct from the existing NEVER-RE-ASK
    rule (which is about not asking a question again) -- this is about HOW
    to voice a reference to something already known when it naturally helps,
    which had zero existing coverage. Shared via GOLDEN_RULES, so present in
    both modes."""
    prompt = build_system_prompt(_property(), None, _user())
    assert "refer back to something already established in this call" in prompt
    lead_prompt = build_lead_system_prompt(_user(), [_property()])
    assert "refer back to something already established in this call" in lead_prompt


def test_golden_rules_covers_comparison_questions_between_recommended_properties():
    """Recommendation engine v2 ("why not that one?" / tradeoff reasoning):
    confirmed via grep there was zero existing coverage for a guest directly
    asking to compare options already recommended -- the model must answer
    using the real difference recommend_properties already returned
    (card.py's new comparison_notes), never invent one or just pick a
    favorite. Shared via GOLDEN_RULES, so present in both modes."""
    prompt = build_system_prompt(_property(), None, _user())
    assert "what's the difference?" in prompt
    assert "using the real difference recommend_properties already gave you" in prompt
    assert "never guess at or invent a difference" in prompt
    lead_prompt = build_lead_system_prompt(_user(), [_property()])
    assert "using the real difference recommend_properties already gave you" in lead_prompt


def test_golden_rules_covers_early_checkin_late_checkout_fees_only_when_asked():
    """Phase 6 (Negotiation engine): a host-configured early_checkin_fee/
    late_checkout_fee must never be volunteered unprompted -- the model
    should call get_pricing with requested_early_checkin/requested_late_checkout
    only when the guest actually asked. Confirmed via grep there was zero
    existing coverage before this clause. Shared via GOLDEN_RULES, so
    present in both modes."""
    prompt = build_system_prompt(_property(), None, _user())
    assert "requested_early_checkin/requested_late_checkout" in prompt
    assert "never volunteered upfront" in prompt
    lead_prompt = build_lead_system_prompt(_user(), [_property()])
    assert "requested_early_checkin/requested_late_checkout" in lead_prompt


def test_golden_rules_covers_weekend_minimum_stay_requirement():
    """Phase 6: a stricter, possibly weekend-only minimum-stay requirement
    (NegotiationRule rule_type="minimum_stay_nights") must be stated
    plainly, never worked around by the model itself. Shared via
    GOLDEN_RULES, so present in both modes."""
    prompt = build_system_prompt(_property(), None, _user())
    assert "stricter on weekends than on weekdays" in prompt
    lead_prompt = build_lead_system_prompt(_user(), [_property()])
    assert "stricter on weekends than on weekdays" in lead_prompt


def test_golden_rules_covers_hard_close_vs_soft_close_framing():
    """Phase 8 (Closing intelligence): the closing line must match how far
    the guest actually got -- reassuring/concrete for a hard close (accepted
    a property, heard a real price), open-ended/non-committal for a soft
    close, never fabricated scarcity/urgency. Confirmed via grep there was
    zero existing coverage before this clause. Shared via GOLDEN_RULES, so
    present in both modes."""
    prompt = build_system_prompt(_property(), None, _user())
    assert "hard close" in prompt.lower()
    assert "soft close" in prompt.lower()
    assert "never invent urgency or scarcity" in prompt.lower()
    lead_prompt = build_lead_system_prompt(_user(), [_property()])
    assert "hard close" in lead_prompt.lower()
    assert "soft close" in lead_prompt.lower()


def test_golden_rules_hard_close_does_not_repeat_host_will_follow_up():
    """Self-review fix: the hard-close reassurance must not tell the model
    to say "the host will follow up soon" again -- that phrase is already
    capped at once per call by the existing escalation rule (line ~240,
    "Never say 'the host will be in touch' more than once"), and the most
    common hard-close path (a guest accepting a price) already triggers
    that phrase once via the escalate_to_host workflow. The hard-close
    clause must explicitly defer to that existing cap, not silently
    duplicate the phrase."""
    prompt = build_system_prompt(_property(), None, _user())
    assert 'do not say "the host will follow up/be in touch" again' in prompt.lower()
    assert "capped at once per call" in prompt.lower()


def test_golden_rules_covers_next_follow_up_must_be_a_concrete_action():
    """Phase 8: next_follow_up must be written as a concrete next action for
    the host, not a vague restatement -- previously unguided. Shared via
    GOLDEN_RULES/LEAD_AGENT_INSTRUCTIONS, so present in the lead-agent mode
    that actually calls update_lead with next_follow_up."""
    lead_prompt = build_lead_system_prompt(_user(), [_property()])
    assert "write next_follow_up as a concrete next" in lead_prompt.lower()
    assert "action for the host to take" in lead_prompt.lower()


def test_golden_rules_covers_escalation_urgency_mapping():
    """Phase 8: escalate_to_host's urgency levels (low/medium/high/emergency)
    had no guidance on how to choose between them -- previously the model
    could pick arbitrarily. Shared via GOLDEN_RULES, so present in both
    modes."""
    prompt = build_system_prompt(_property(), None, _user())
    assert "set urgency honestly" in prompt.lower()
    assert "never default to high/emergency" in prompt.lower()
    lead_prompt = build_lead_system_prompt(_user(), [_property()])
    assert "set urgency honestly" in lead_prompt.lower()


def test_golden_rules_covers_recommendation_refinement_is_additive_not_replacement():
    """Recommendation conversations ("Phase X"): a guest narrowing down
    ("something cheaper", "anything with a pool?", "more premium") must be
    told to call recommend_properties with the new criterion ADDED to
    everything already established, never as a replacement -- and to use
    cheaper_than_shown/larger_than_shown rather than inventing a number.
    Confirmed via grep there was zero existing coverage before this clause.
    Shared via GOLDEN_RULES, so present in both modes."""
    prompt = build_system_prompt(_property(), None, _user())
    assert "ADDED to everything already established this call" in prompt
    assert "cheaper_than_shown/larger_than_shown to true" in prompt
    assert "rupee figure or a guest count yourself" in prompt
    lead_prompt = build_lead_system_prompt(_user(), [_property()])
    assert "ADDED to everything already established this call" in lead_prompt
    assert "cheaper_than_shown/larger_than_shown to true" in lead_prompt


def test_golden_rules_covers_amenity_checklist_must_state_present_and_missing():
    """Recommendation conversations ("Phase X"): required_amenities became a
    soft ranking preference (never a hard filter, see filter_builder.py's
    apply_amenity_boost), so a returned property can genuinely have only
    SOME of what the guest asked for -- per explicit product direction, both
    what's present AND what's missing must be spoken so the guest can
    decide. Shared via GOLDEN_RULES, so present in both modes."""
    prompt = build_system_prompt(_property(), None, _user())
    assert "say both explicitly" in prompt
    assert "it has the pool you wanted, but isn't pet friendly" in prompt
    lead_prompt = build_lead_system_prompt(_user(), [_property()])
    assert "say both explicitly" in lead_prompt


def test_golden_rules_extracts_composite_and_implicit_guest_counts():
    """Intelligent slot collection ("Phase 3"): the existing extract-
    indirectly guidance only covered a direct number stated in words
    ("we are 10 friends"). Guest counts are just as often implicit ("my
    wife and I") or composite ("2 adults and a kid") -- neither was
    previously covered, confirmed by grep finding zero matches for either
    shape before this change. Shared via GOLDEN_RULES, so present in both
    modes."""
    prompt = build_system_prompt(_property(), None, _user())
    assert "\"my wife and I\"" in prompt
    assert "num_guests=2" in prompt
    assert "\"2 adults and a kid\"" in prompt
    assert "num_guests=3" in prompt
    lead_prompt = build_lead_system_prompt(_user(), [_property()])
    assert "\"my wife and I\"" in lead_prompt


def test_golden_rules_treats_a_named_locality_as_a_usable_answer():
    """Intelligent slot collection: "near Baga" is already handled correctly
    downstream once passed through as preferred_location (filter_builder.py's
    fuzzy locality matching against Property.city/neighborhood_info) -- the
    previously-missing piece was telling the model this IS a usable answer,
    not a vague one needing a follow-up.

    Self-review caught that the first version of this clause also listed
    "walking distance from the beach" as an equivalent example -- but that
    phrase doesn't reliably map to EITHER preferred_location (it's not a
    place name, so filter_builder.py's ilike locality match wouldn't find
    it) or near_landmark (matches_landmark's fuzzy match compares against a
    specific named place like "Thalassa", not a generic feature-distance
    description -- confirmed by reading the actual matching code, not
    assumed). Narrowed to only the example that's actually verified to work
    end-to-end, rather than asserting a match path that doesn't exist."""
    prompt = build_system_prompt(_property(), None, _user())
    assert "\"near Baga\"" in prompt
    assert "walking distance from the beach" not in prompt


def test_golden_rules_treats_under_x_budget_as_an_exact_ceiling():
    """Intelligent slot collection: "under 8k" already gives an exact
    number (8000) usable directly as budget -- the model shouldn't ask the
    guest to restate it as a fixed amount first."""
    prompt = build_system_prompt(_property(), None, _user())
    assert "\"under 8k\"" in prompt
    assert "already gives you an exact ceiling to search with" in prompt


def test_guest_support_has_its_own_name_phone_timing_guidance():
    """Phase 6.1 audit (documentation/agent-conversation-improvement.md):
    GOLDEN_RULES' conversational-warmth section and _caller_phone_section
    both say "see the lead qualification workflow's ... timing" for exactly
    when to ask for name/phone -- a dangling reference in Guest Support mode,
    since LEAD_AGENT_INSTRUCTIONS (where that timing actually lives) is never
    included in a Guest Support prompt at all. GUEST_SUPPORT_INSTRUCTIONS
    must carry its own local equivalent instead of relying on a cross-
    reference into a block that isn't there."""
    prompt = build_system_prompt(_property(), None, _user())
    assert "rarely a reason to actively ask for the" in prompt
    assert "never as a routine opener" in prompt
    # Confirm this guidance is NOT duplicated into the Lead Agent prompt --
    # that mode already has its own real timing rule (step 5), this is
    # Guest-Support-specific text.
    lead_prompt = build_lead_system_prompt(_user(), [_property()])
    assert "rarely a reason to actively ask for the" not in lead_prompt


def test_lead_agent_recommends_before_asking_budget_when_other_criteria_known():
    """Phase 2.3 (documentation/agent-conversation-improvement.md,
    requirement #3): the dates-finalized YES branch must not gate the first
    recommendation on asking budget first when location/purpose is already
    known -- confirmed the sharpened wording landed, not just the original
    'ask their budget, then use recommend_properties' sequencing."""
    prompt = build_lead_system_prompt(_user(), [_property()])
    assert "recommend now with what you already have" in prompt
    assert "don't gate the first recommendation on" in prompt


def test_golden_rules_explicit_language_request_recognized_immediately():
    """Phase 3.3 (documentation/agent-conversation-improvement.md, catalogue
    item C5): a guest directly asking 'can you speak Hindi?' must be
    recognized as information worth acting on immediately (same weight as
    stating a name/phone), not left to passive per-turn mirroring alone --
    confirmed live, the reply stayed in English after this exact question."""
    prompt = build_system_prompt(_property(), None, _user())
    assert "EXPLICITLY asks you to speak a specific language" in prompt
    assert "call update_lead with preferred_language" in prompt
    assert "must be honored immediately, not eventually" in prompt


def test_golden_rules_explicit_language_request_also_present_in_lead_agent_prompt():
    prompt = build_lead_system_prompt(_user(), [_property()])
    assert "EXPLICITLY asks you to speak a specific language" in prompt


def test_unset_agent_language_policy_is_byte_identical_to_no_policy_field():
    """Phase 3.3: no regression for the overwhelming majority of hosts who
    won't set this -- an unset (None) agent_language_policy must produce a
    prompt with no policy line at all, same as before this task existed."""
    prompt = build_system_prompt(_property(), None, _user())
    assert "generally prefer" not in prompt


def test_agent_language_policy_hindi_first_adds_baseline_note():
    host = _user(agent_language_policy="hindi_first")
    prompt = build_system_prompt(_property(), None, host)
    assert "guests generally prefer Hindi/Hinglish" in prompt
    assert "still switching to whatever the guest actually uses" in prompt


def test_agent_language_policy_english_first_adds_baseline_note():
    host = _user(agent_language_policy="english_first")
    prompt = build_system_prompt(_property(), None, host)
    assert "guests generally prefer English" in prompt


def test_agent_language_policy_present_in_lead_agent_prompt_too():
    host = _user(agent_language_policy="hindi_first")
    prompt = build_lead_system_prompt(host, [_property()])
    assert "guests generally prefer Hindi/Hinglish" in prompt
