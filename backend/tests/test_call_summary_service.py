import json

import pytest

from app.services import call_summary_service


async def test_summarize_call_with_empty_transcript_short_circuits():
    summary = await call_summary_service.summarize_call(None, None)
    assert summary.outcome.status == "No Booking"
    assert summary.booking_snapshot.guest_name == "Unknown"
    assert "Check-in date" in summary.missing_information


async def test_summarize_call_with_too_short_duration_short_circuits():
    summary = await call_summary_service.summarize_call("user: hi", 1.0)
    assert summary.outcome.status == "No Booking"
    assert "too short" in summary.conversation_summary


def test_parse_summary_response_happy_path():
    raw = json.dumps(
        {
            "booking_snapshot": {
                "guest_name": "Rohan",
                "intent": "New Booking",
                "check_in": "29 May",
                "check_out": "Not provided",
                "nights": "Unknown",
                "guests": "8",
                "property": ["Pause Project Goa"],
                "budget": "Not mentioned",
                "occasion": "Birthday",
                "language": "Hinglish",
            },
            "conversation_summary": "Guest asked about availability for a birthday trip to Goa.",
            "outcome": {"status": "Awaiting Guest Details", "reason": "Check-out date not yet confirmed."},
            "host_action": ["Confirm availability for 29 May."],
            "key_details": ["Group of 8 friends", "Birthday celebration"],
            "missing_information": ["Check-out date", "Budget"],
        }
    )
    summary = call_summary_service._parse_summary_response(raw)
    assert summary.booking_snapshot.guest_name == "Rohan"
    assert summary.booking_snapshot.property == ["Pause Project Goa"]
    assert summary.outcome.status == "Awaiting Guest Details"
    assert summary.host_action == ["Confirm availability for 29 May."]
    assert summary.missing_information == ["Check-out date", "Budget"]


def test_parse_summary_response_defends_against_missing_fields():
    # Model omitted everything except conversation_summary -- every other
    # field must degrade to CallSummary's own safe defaults, never raise.
    raw = json.dumps({"conversation_summary": "Spam call, hung up immediately."})
    summary = call_summary_service._parse_summary_response(raw)
    assert summary.booking_snapshot.guest_name == "Unknown"
    assert summary.booking_snapshot.property == []
    assert summary.outcome.status == "No Booking"
    assert summary.host_action == []
    assert summary.conversation_summary == "Spam call, hung up immediately."


def test_parse_summary_response_rejects_invalid_json():
    with pytest.raises(call_summary_service.CallSummaryError):
        call_summary_service._parse_summary_response("not json")


def test_parse_summary_response_rejects_non_object():
    with pytest.raises(call_summary_service.CallSummaryError):
        call_summary_service._parse_summary_response(json.dumps(["a", "list"]))


def test_parse_summary_response_keeps_valid_objection_tags():
    raw = json.dumps(
        {
            "conversation_summary": "Guest pushed back on price then went silent.",
            "objection_tags": ["PRICE_TOO_HIGH", "GUEST_STOPPED_RESPONDING"],
        }
    )
    summary = call_summary_service._parse_summary_response(raw)
    assert summary.objection_tags == ["PRICE_TOO_HIGH", "GUEST_STOPPED_RESPONDING"]


def test_parse_summary_response_drops_hallucinated_objection_tags():
    # A tag outside the fixed vocabulary (OBJECTION_TAGS) must never survive
    # parsing, even if the model invents a plausible-looking one -- the
    # controlled vocabulary is enforced in code, not just prompt wording.
    raw = json.dumps(
        {
            "conversation_summary": "Guest asked something unusual.",
            "objection_tags": ["PRICE_TOO_HIGH", "SOME_MADE_UP_TAG", "another_bad_one"],
        }
    )
    summary = call_summary_service._parse_summary_response(raw)
    assert summary.objection_tags == ["PRICE_TOO_HIGH"]


def test_parse_summary_response_dedupes_objection_tags():
    raw = json.dumps(
        {
            "conversation_summary": "Guest repeated their price concern.",
            "objection_tags": ["PRICE_TOO_HIGH", "PRICE_TOO_HIGH"],
        }
    )
    summary = call_summary_service._parse_summary_response(raw)
    assert summary.objection_tags == ["PRICE_TOO_HIGH"]


def test_parse_summary_response_defaults_objection_tags_to_empty_list():
    raw = json.dumps({"conversation_summary": "No objection info present."})
    summary = call_summary_service._parse_summary_response(raw)
    assert summary.objection_tags == []


def test_parse_summary_response_keeps_no_objection_tag():
    raw = json.dumps(
        {
            "conversation_summary": "Smooth booking, no friction.",
            "objection_tags": ["NO_OBJECTION"],
        }
    )
    summary = call_summary_service._parse_summary_response(raw)
    assert summary.objection_tags == ["NO_OBJECTION"]
