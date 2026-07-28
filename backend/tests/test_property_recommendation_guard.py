import uuid

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
from app.services.property.pitch_formatter import RecommendationResult
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
