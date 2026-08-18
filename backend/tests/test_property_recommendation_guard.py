import uuid
from datetime import date

import pytest
from pipecat.frames.frames import (
    FunctionCallFromLLM,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.tests.utils import run_test

from app.services.property.card import PropertyCard
from app.services.property.pitch_formatter import PartiallyAvailableProperty, RecommendationResult
from app.voice.property_recommendation_guard import (
    PropertyRecommendationGuardProcessor,
    strip_property_ids,
)


def _card(name: str, price: float = 3500, guests: int = 2, **overrides) -> PropertyCard:
    defaults = dict(
        property_id=uuid.uuid4(),
        spoken_name=name,
        display_name=name,
        city="South Goa",
        property_type=None,
        bedroom_count=None,
        base_price=price,
        max_guests=guests,
        top_amenities=[],
        usp=None,
        match_reasons=[],
        comparison_note="",
        is_premium=False,
        amenity_checklist="",
    )
    defaults.update(overrides)
    return PropertyCard(**defaults)


_RESULT = RecommendationResult(
    options=[
        _card("Cabana 1BHK", price=0),
        _card("Azure 1BHK", price=3500),
    ]
)


def _response(*chunks: str) -> list:
    return [LLMFullResponseStartFrame(), *[LLMTextFrame(c) for c in chunks], LLMFullResponseEndFrame()]


def _call_started(name: str) -> FunctionCallsStartedFrame:
    return FunctionCallsStartedFrame(
        function_calls=[FunctionCallFromLLM(function_name=name, tool_call_id="tc_1", arguments={}, context=None)]
    )


def test_record_tool_result_extracts_spoken_names_from_cards():
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("recommend_properties", _RESULT)
    assert [o["name"] for o in guard._pending_options] == ["Cabana 1BHK", "Azure 1BHK"]


def test_record_tool_result_ignores_not_found_result():
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("recommend_properties", RecommendationResult(options=[], not_found=True))
    assert guard._pending_options == []


def test_record_tool_result_ignores_non_recommendation_result_shape():
    # Defensive: if something other than a RecommendationResult is ever
    # passed (a bug elsewhere), the guard must not crash trying to read
    # .options off it -- just treat it as "nothing to verify."
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("recommend_properties", "not a RecommendationResult")
    assert guard._pending_options == []


def test_pipe_in_property_name_never_torn_apart_by_the_guard():
    # Regression guard for the underlying bug that used to require regex-
    # parsing rendered text: a real property name containing a literal "|"
    # (imported Airbnb titles like "Azure 1bhk | 5 mins walk to beach |
    # Pause Project") must pass through the guard's bookkeeping completely
    # intact now that it's handed structured PropertyCards directly,
    # instead of being re-split out of rendered speech text.
    name_with_pipes = "Azure 1bhk | 5 mins walk to beach | Pause Project"
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result(
        "recommend_properties", RecommendationResult(options=[_card(name_with_pipes)])
    )
    assert guard._pending_options[0]["name"] == name_with_pipes


def test_strip_property_ids_removes_the_whole_aside():
    text = "Cabana 1BHK is great (property_id: 48c687d2-7be8-435c-951c-080d5bab0314) for two guests."
    assert strip_property_ids(text) == "Cabana 1BHK is great for two guests."


@pytest.mark.asyncio
async def test_regression_property_id_stripped_from_speech():
    """Regression for the exact live failure on 2026-07-27: '(property ID
    48c687d2-7be8-435c-951c-080d5bab0314)' was spoken verbatim to a guest."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("recommend_properties", _RESULT)

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")]
        + _response(
            "Here are two South-Goa options: Cabana 1BHK, five minutes from the beach, at zero rupees per "
            "night. (property_id: 48c687d2-7be8-435c-951c-080d5bab0314) Which one sounds interesting?"
        ),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert "48c687d2" not in text
    assert "property_id" not in text.lower()
    assert "Cabana 1BHK" in text


@pytest.mark.asyncio
async def test_regression_unnamed_recommendation_gets_a_real_fallback():
    """Regression for the exact live failure on 2026-07-27: recommend_properties
    returned real options, and the next reply skipped straight to 'Which one
    sounds interesting?' without ever naming a single property -- the guest
    had to say "You didn't recommend any properties" to get a real answer."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("recommend_properties", _RESULT)

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")]
        + _response("Great choices. Which one sounds interesting?"),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert "Cabana 1BHK" in text
    assert "Azure 1BHK" in text
    assert "48c687d2" not in text


@pytest.mark.asyncio
async def test_reply_that_does_name_a_property_passes_through_unmodified_besides_id():
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("recommend_properties", _RESULT)

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")]
        + _response("The Cabana 1BHK is five minutes from the beach at zero rupees per night. Interested?"),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == "The Cabana 1BHK is five minutes from the beach at zero rupees per night. Interested?"


@pytest.mark.asyncio
async def test_id_leak_guard_also_arms_on_get_pricing():
    guard = PropertyRecommendationGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("get_pricing")]
        + _response(
            "That comes to nine thousand rupees total for the stay at property_id "
            "48c687d2-7be8-435c-951c-080d5bab0314."
        ),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert "48c687d2" not in text
    assert "nine thousand rupees" in text


@pytest.mark.asyncio
async def test_unrelated_tool_call_does_not_arm_the_guard():
    guard = PropertyRecommendationGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("update_lead")] + _response("Got it, noted."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == "Got it, noted."


@pytest.mark.asyncio
async def test_no_tool_call_at_all_passes_text_through_unbuffered():
    guard = PropertyRecommendationGuardProcessor()

    down_frames, _ = await run_test(guard, frames_to_send=_response("Sure, how can I help?"))

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == "Sure, how can I help?"


@pytest.mark.asyncio
async def test_guard_disarms_after_one_response_even_without_a_match():
    """A second, unrelated turn after the armed one must not get retroactively
    rewritten/blocked -- only the one response immediately after the tool call
    is buffered."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("recommend_properties", _RESULT)

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")]
        + _response("The Cabana 1BHK sounds perfect for you.")
        + _response("Anything else I can help with?"),
    )

    text_frames = [f.text for f in down_frames if isinstance(f, LLMTextFrame)]
    assert text_frames[-1] == "Anything else I can help with?"


# --- Phase 4b.1: price fidelity (get_pricing / negotiate_rate) ---


@pytest.mark.asyncio
async def test_get_pricing_reply_stating_a_different_total_gets_corrected():
    """The tool computed ₹18,700; the model's reply states a different
    number entirely -- must be overridden with the real total."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("get_pricing", {"property_name": "Ocean View Villa", "total": 18700})

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("get_pricing")]
        + _response("That comes to ₹12,000 total for Ocean View Villa, all inclusive."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert "18,700" in text
    assert "12,000" not in text


@pytest.mark.asyncio
async def test_get_pricing_reply_stating_the_correct_total_passes_through_unmodified():
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("get_pricing", {"property_name": "Ocean View Villa", "total": 18700})

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("get_pricing")]
        + _response("That comes to ₹18,700 total for Ocean View Villa, all inclusive."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == "That comes to ₹18,700 total for Ocean View Villa, all inclusive."


@pytest.mark.asyncio
async def test_negotiate_rate_reply_stating_a_different_counter_offer_gets_corrected():
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("negotiate_rate", {"property_name": "Palm Retreat", "total": 9500})

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("negotiate_rate")]
        + _response("I can offer you Palm Retreat at ₹7,000 for your stay."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert "9,500" in text
    assert "7,000" not in text


@pytest.mark.asyncio
async def test_price_fidelity_check_is_a_noop_without_a_recorded_fact():
    """No price fact was ever recorded (e.g. get_pricing's own early-return
    error paths never call on_priced) -- must never rewrite the text."""
    guard = PropertyRecommendationGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("get_pricing")]
        + _response("I couldn't find that property to price."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == "I couldn't find that property to price."


# --- Phase 4b.1: availability fidelity (check_calendar) ---


@pytest.mark.asyncio
async def test_check_calendar_reply_contradicting_true_availability_gets_corrected():
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("check_calendar", {"property_name": "Ocean View Villa", "available": True})

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("check_calendar")]
        + _response("Unfortunately, Ocean View Villa is not available for those dates."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == "Ocean View Villa is available for those dates."


@pytest.mark.asyncio
async def test_check_calendar_reply_contradicting_false_availability_gets_corrected():
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("check_calendar", {"property_name": "Ocean View Villa", "available": False})

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("check_calendar")]
        + _response("Good news, Ocean View Villa is available for those dates!"),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == "Ocean View Villa is not available for those dates."


@pytest.mark.asyncio
async def test_check_calendar_reply_matching_real_availability_passes_through_unmodified():
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("check_calendar", {"property_name": "Ocean View Villa", "available": True})

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("check_calendar")]
        + _response("Ocean View Villa is available from the 10th to the 12th."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == "Ocean View Villa is available from the 10th to the 12th."


@pytest.mark.asyncio
async def test_check_calendar_reply_with_neither_assertion_is_left_alone():
    """A reply that doesn't clearly assert either way (relays dates/a
    window without using 'available'/'not available' at all) must not be
    guessed at -- this check only catches a CLEAR contradiction."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("check_calendar", {"property_name": "Ocean View Villa", "available": False})

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("check_calendar")]
        + _response("The next open window for Ocean View Villa is the 15th to the 18th."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == "The next open window for Ocean View Villa is the 15th to the 18th."


# --- Phase 4b.2: capacity fidelity (recommend_properties) ---


@pytest.mark.asyncio
async def test_recommend_properties_reply_misstating_capacity_gets_corrected():
    """RecommendationResult already only contains properties that fit the
    guest's real count (Phase 1.4's SQL filter) -- this is the
    belt-and-suspenders check that even a correctly-filtered result is
    SPOKEN correctly. Azure 1BHK really sleeps 2; the reply claims 6."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result(
        "recommend_properties", RecommendationResult(options=[_card("Azure 1BHK", guests=2)])
    )

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")]
        + _response("Azure 1BHK is a lovely spot, sleeps 6. Interested?"),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert "sleeping 2" in text
    assert "sleeps 6" not in text


@pytest.mark.asyncio
async def test_recommend_properties_reply_with_correct_capacity_passes_through_unmodified():
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result(
        "recommend_properties", RecommendationResult(options=[_card("Azure 1BHK", guests=2)])
    )

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")]
        + _response("Azure 1BHK is a lovely spot, sleeps 2. Interested?"),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == "Azure 1BHK is a lovely spot, sleeps 2. Interested?"


# --- Availability-first recommendations, Implementation 5: partial-availability fidelity ---


def _partial_result(name: str, conflicts: list[tuple]) -> RecommendationResult:
    return RecommendationResult(options=[], partially_available=[PartiallyAvailableProperty(name, conflicts)])


@pytest.mark.asyncio
async def test_recommend_properties_reply_falsely_claiming_a_partial_property_is_available_gets_corrected():
    """Self-review fix: an earlier version of this check overrode the WHOLE
    reply, which silently discarded anything else correctly said in the same
    turn (confirmed live via a direct repro: a correctly-named full-match
    property mentioned earlier in the same reply was erased). The
    correction is now APPENDED -- the false claim itself stays in the text
    (a real, if imperfect, tradeoff versus a full rewrite), but the real
    conflicting dates always reach the guest immediately after it, and
    nothing else correctly said in the same reply is ever silently dropped."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result(
        "recommend_properties",
        _partial_result("Riverbend Cottage", [(date(2026, 10, 3), date(2026, 10, 5))]),
    )

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")]
        + _response("Great news, Riverbend Cottage is available for those dates!"),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert "2026-10-03 to 2026-10-05" in text
    assert text.startswith("Great news, Riverbend Cottage is available for those dates!")


@pytest.mark.asyncio
async def test_recommend_properties_reply_falsely_claiming_availability_preserves_other_correct_content():
    """The exact bug this fix closes: a reply correctly naming a full-match
    property AND separately, falsely claiming a partial property is
    available -- the full-match property's correct mention must survive."""
    guard = PropertyRecommendationGuardProcessor()
    result = RecommendationResult(
        options=[_card("Azure 1BHK", guests=2)],
        partially_available=[
            PartiallyAvailableProperty("Riverbend Cottage", [(date(2026, 10, 3), date(2026, 10, 5))])
        ],
    )
    guard.record_tool_result("recommend_properties", result)

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")]
        + _response("Azure 1BHK sleeps 2 and is a great fit. Riverbend Cottage is available for those dates too!"),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert "Azure 1BHK sleeps 2 and is a great fit." in text
    assert "2026-10-03 to 2026-10-05" in text


@pytest.mark.asyncio
async def test_recommend_properties_reply_correctly_naming_partial_conflict_dates_passes_through_unmodified():

    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result(
        "recommend_properties",
        _partial_result("Riverbend Cottage", [(date(2026, 10, 3), date(2026, 10, 5))]),
    )
    reply = (
        "Riverbend Cottage has a booking from 2026-10-03 to 2026-10-05 that overlaps part of the "
        "requested dates. Once your dates are finalized, I can check again."
    )

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")] + _response(reply),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == reply


@pytest.mark.asyncio
async def test_recommend_properties_reply_correctly_naming_partial_dates_rephrased_day_first_is_not_flagged():
    """Self-review fix: a reply correctly stating the real conflicting dates
    but rephrased ('the 3rd to the 5th of October' instead of an ISO/
    month-first echo) must NOT be treated as an omission or a wrong-date
    fabrication -- confirmed via a direct repro before this fix that it was
    (redundant duplicate correction appended onto an already-correct
    reply)."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result(
        "recommend_properties",
        _partial_result("Riverbend Cottage", [(date(2026, 10, 3), date(2026, 10, 5))]),
    )
    reply = (
        "Riverbend Cottage has a booking from the 3rd to the 5th of October, so it is not available "
        "for those exact dates."
    )

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")] + _response(reply),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == reply


@pytest.mark.asyncio
async def test_recommend_properties_reply_with_wrong_day_first_dates_still_gets_corrected():
    """The day-first rephrasing acceptance above must not become a
    loophole -- a WRONG day-first date must still be caught as case 2."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result(
        "recommend_properties",
        _partial_result("Riverbend Cottage", [(date(2026, 10, 3), date(2026, 10, 5))]),
    )

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")]
        + _response("Riverbend Cottage has a booking from the 10th to the 12th of October."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert "2026-10-03 to 2026-10-05" in text


@pytest.mark.asyncio
async def test_recommend_properties_reply_stating_unrelated_numbers_near_wrong_month_still_gets_corrected():
    """Task 5.2's review found a real false-positive: an earlier version of
    _real_dates_stated collected every bare number in the WHOLE sentence, so
    a reply naming the wrong month ('May 3rd') alongside an unrelated number
    for a different reason ('5 bedrooms') coincidentally matched the real
    conflict's day numbers (3, 5) and was wrongly treated as already
    correct -- passing through unmodified despite never stating the real
    Oct 3-5 conflict at all. Must still be corrected."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result(
        "recommend_properties",
        _partial_result("Riverbend Cottage", [(date(2026, 10, 3), date(2026, 10, 5))]),
    )

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")]
        + _response(
            "Riverbend Cottage was renovated on May 3rd and has 5 bedrooms, "
            "so it is not available for your dates."
        ),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert "2026-10-03 to 2026-10-05" in text


@pytest.mark.asyncio
async def test_recommend_properties_reply_correctly_naming_partial_dates_with_abbreviated_month_is_not_flagged():
    """Task 5.2's review found a real gap: an ordinary abbreviated-month
    rephrasing ('Oct 3 through Oct 5') wasn't recognized as a date at all,
    causing a redundant duplicate correction on an already-correct reply."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result(
        "recommend_properties",
        _partial_result("Riverbend Cottage", [(date(2026, 10, 3), date(2026, 10, 5))]),
    )
    reply = "Riverbend Cottage is booked Oct 3 through Oct 5, so it may not be free for your exact dates."

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")] + _response(reply),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == reply


@pytest.mark.asyncio
async def test_recommend_properties_reply_with_wrong_dates_for_partial_property_gets_corrected():
    """Self-review find: the model states a SPECIFIC date for the partial
    property, but it's not the real conflicting range -- a fabrication, not
    an omission. The real dates are appended (not a whole-reply override --
    same fix as the false-claim case above, for the same reason: never
    silently discard other correct content in the same reply); the guest
    still ends up with the real dates immediately after the wrong ones."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result(
        "recommend_properties",
        _partial_result("Riverbend Cottage", [(date(2026, 10, 3), date(2026, 10, 5))]),
    )

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")]
        + _response("Riverbend Cottage is not available from October 10th to 12th."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text.startswith("Riverbend Cottage is not available from October 10th to 12th.")
    assert "2026-10-03 to 2026-10-05" in text


@pytest.mark.asyncio
async def test_recommend_properties_reply_omitting_partial_conflict_dates_gets_dates_appended():
    """Task 4.3's reverify: a bare 'No.'/vague deflection with zero
    conflicting dates named -- a real, reproducible failure shape on a small
    model, distinct from falsely claiming availability. The property IS
    named and the reply does NOT claim it's available, but never states why
    -- the real dates are appended, not a full-reply replacement (unlike the
    false-claim case above), since the surrounding reply is usually still a
    reasonable thing to have said."""

    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result(
        "recommend_properties",
        _partial_result("Riverbend Cottage", [(date(2026, 10, 3), date(2026, 10, 5))]),
    )

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")]
        + _response("Riverbend Cottage is not available for those dates."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text.startswith("Riverbend Cottage is not available for those dates.")
    assert "2026-10-03 to 2026-10-05" in text


@pytest.mark.asyncio
async def test_recommend_properties_reply_with_bare_no_gets_dates_appended():
    """The exact reproduced failure shape from Task 4.3's reverify: a bare
    'No.' naming the property but nothing else."""

    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result(
        "recommend_properties",
        _partial_result("Riverbend Cottage", [(date(2026, 10, 3), date(2026, 10, 5))]),
    )

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")]
        + _response("No, Riverbend Cottage isn't available for those exact dates."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert "2026-10-03 to 2026-10-05" in text


@pytest.mark.asyncio
async def test_recommend_properties_reply_never_mentioning_partial_property_is_left_alone():
    """A reply that doesn't name the partial property at all this turn (e.g.
    still asking a different question first) is a legitimate turn, not a
    violation -- only correct what's actually said, same discipline every
    other check in this file already follows."""

    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result(
        "recommend_properties",
        _partial_result("Riverbend Cottage", [(date(2026, 10, 3), date(2026, 10, 5))]),
    )

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")]
        + _response("What dates are you thinking of for your trip?"),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == "What dates are you thinking of for your trip?"


@pytest.mark.asyncio
async def test_recommend_properties_capacity_fallback_supersedes_partial_check_in_same_turn():
    """Self-review find while testing composition: the EXISTING capacity/
    name-check fallback (_fallback_recommendation_text) replaces the whole
    reply with a listing of only the full-match `options` -- it never
    mentions a partial property at all. Once that fallback fires, the
    partial-availability check correctly finds nothing to correct (the
    property it cares about is no longer named in the text at all, so
    nothing false or incomplete is being said about it either) -- this is
    the existing, pre-Implementation-5 fallback's own scope, not a gap this
    task introduces. Confirms this composes safely (no crash, no incorrect
    re-injection of a property name the fallback deliberately dropped),
    which is the actual thing worth pinning here."""
    guard = PropertyRecommendationGuardProcessor()
    result = RecommendationResult(
        options=[_card("Azure 1BHK", guests=2)],
        partially_available=[
            PartiallyAvailableProperty("Riverbend Cottage", [(date(2026, 10, 3), date(2026, 10, 5))])
        ],
    )
    guard.record_tool_result("recommend_properties", result)

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")]
        + _response("Azure 1BHK sleeps 6. Riverbend Cottage isn't available."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert "sleeping 2" in text
    assert "sleeps 6" not in text
    assert "Riverbend Cottage" not in text
    assert "2026-10-03 to 2026-10-05" not in text


@pytest.mark.asyncio
async def test_recommend_properties_partial_fidelity_composes_with_a_correctly_named_full_match_same_turn():
    """Genuine composition case: a reply that correctly names a full-match
    property (no capacity/name violation to trigger the wholesale fallback)
    AND separately omits a partial property's conflicting dates -- both
    facts must be checked and the reply must end up correct on the part
    that was wrong, without disturbing the part that was already right."""
    guard = PropertyRecommendationGuardProcessor()
    result = RecommendationResult(
        options=[_card("Azure 1BHK", guests=2)],
        partially_available=[
            PartiallyAvailableProperty("Riverbend Cottage", [(date(2026, 10, 3), date(2026, 10, 5))])
        ],
    )
    guard.record_tool_result("recommend_properties", result)

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")]
        + _response("Azure 1BHK sleeps 2, a great fit. Riverbend Cottage isn't available for those dates."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert "sleeps 2" in text
    assert "2026-10-03 to 2026-10-05" in text


@pytest.mark.asyncio
async def test_recommend_properties_partial_fidelity_is_a_noop_without_any_partial_facts():
    """No partial_facts were ever recorded (the common case -- most calls
    have zero partial-availability properties) -- must never touch the
    text."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("recommend_properties", RecommendationResult(options=[_card("Azure 1BHK")]))

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")] + _response("Azure 1BHK sleeps 2. Interested?"),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == "Azure 1BHK sleeps 2. Interested?"


def test_record_tool_result_extracts_partial_facts_with_iso_dates():
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result(
        "recommend_properties",
        _partial_result("Riverbend Cottage", [(date(2026, 10, 3), date(2026, 10, 5))]),
    )
    assert guard._pending_partial_facts == [
        {
            "name": "Riverbend Cottage",
            "conflicting_bookings": ["2026-10-03 to 2026-10-05"],
            "conflicting_days": [3, 5],
        }
    ]


def test_record_tool_result_extracts_conflicting_days_across_multiple_bookings():
    """conflicting_days spans EVERY conflicting booking's day numbers, not
    just the first -- used by _real_dates_stated's natural-language
    rephrasing check."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result(
        "recommend_properties",
        _partial_result(
            "Riverbend Cottage",
            [(date(2026, 10, 3), date(2026, 10, 5)), (date(2026, 10, 20), date(2026, 10, 22))],
        ),
    )
    assert guard._pending_partial_facts[0]["conflicting_days"] == [3, 5, 20, 22]


@pytest.mark.asyncio
async def test_search_faq_reply_inventing_an_amenity_not_on_file_gets_corrected():
    """The property's real amenities are pool/wifi only -- a reply claiming
    a projector (never on file) must be corrected to the safe fallback."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("search_faq", {"amenities": ["Private pool", "wifi"]})

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("search_faq")]
        + _response("Yes, this property has a projector for movie nights."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == (
        "I don't have verified information about that on file. Let me check with the host so you get the "
        "correct details."
    )


@pytest.mark.asyncio
async def test_search_faq_reply_naming_a_real_amenity_passes_through_unmodified():
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("search_faq", {"amenities": ["Private pool", "wifi"]})

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("search_faq")] + _response("Yes, this property has a private pool."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == "Yes, this property has a private pool."


@pytest.mark.asyncio
async def test_search_faq_reply_with_a_synonym_of_a_real_amenity_is_not_flagged():
    """'Swimming pool' and 'Private pool' both canonicalize to 'pool' --
    a paraphrase using a different but equivalent wording must not be
    treated as an invented amenity."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("search_faq", {"amenities": ["Private pool"]})

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("search_faq")]
        + _response("Yes, there's a swimming pool on site."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == "Yes, there's a swimming pool on site."


@pytest.mark.asyncio
async def test_search_faq_reply_with_free_text_not_matching_any_known_keyword_is_not_flagged():
    """A free-text paraphrase of a real, on-file fact that isn't one of the
    fixed amenity keywords at all (e.g. check-in time) must never be
    flagged -- this check only catches a SPECIFIC recognized amenity
    keyword absent from the real list, not a full semantic check."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result("search_faq", {"amenities": ["wifi"]})

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("search_faq")]
        + _response("Check-in is at 2 PM and check-out is at 11 AM."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == "Check-in is at 2 PM and check-out is at 11 AM."


@pytest.mark.asyncio
async def test_search_faq_fidelity_check_is_a_noop_without_a_recorded_fact():
    """No amenities fact was ever recorded (e.g. a portfolio-wide query with
    no property resolved) -- must never rewrite the text."""
    guard = PropertyRecommendationGuardProcessor()

    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("search_faq")]
        + _response("I don't have verified information about that on file."),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == "I don't have verified information about that on file."


@pytest.mark.asyncio
async def test_recommend_properties_multiple_options_each_with_own_correct_capacity_is_not_flagged():
    """Regression guard for the naive global-scan approach: two DIFFERENT
    correctly-named properties, each correctly stating its OWN (different)
    real capacity, must never be flagged just because some other option's
    number appears elsewhere in the same text."""
    guard = PropertyRecommendationGuardProcessor()
    guard.record_tool_result(
        "recommend_properties",
        RecommendationResult(options=[_card("Azure 1BHK", guests=2), _card("Palm Retreat", guests=6)]),
    )

    text_in = "Azure 1BHK sleeps 2, and Palm Retreat sleeps 6. Which sounds better?"
    down_frames, _ = await run_test(
        guard,
        frames_to_send=[_call_started("recommend_properties")] + _response(text_in),
    )

    text = "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))
    assert text == text_in
