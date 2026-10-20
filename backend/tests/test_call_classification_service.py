"""Staff-engineer review finding: call_classification_service had zero test
coverage despite being the single centralized place that decides a call's
CallType (which gates Lead visibility via lead_service.delete_for_unqualified_call
-- see the module's own docstring). Covers the pure pieces (_pre_check,
_parse_classification_response) directly and classify_call's fallback/
never-raises contract via a monkeypatched LLM call."""

import json

import pytest

from app.services import call_classification_service as svc


def test_pre_check_flags_empty_transcript_as_incomplete():
    result = svc._pre_check(None, duration_seconds=30.0)
    assert result is not None
    assert result.call_type == "INCOMPLETE"
    assert result.confidence == 1.0


def test_pre_check_flags_short_call_as_incomplete():
    result = svc._pre_check("Hello?", duration_seconds=1.0)
    assert result is not None
    assert result.call_type == "INCOMPLETE"


def test_pre_check_passes_through_a_real_call():
    result = svc._pre_check("Guest: Do you have anything free next weekend?", duration_seconds=45.0)
    assert result is None


def test_parse_classification_response_happy_path():
    raw = json.dumps({"call_type": "booking_lead", "confidence": 0.9, "reason": "Asked about availability."})
    result = svc._parse_classification_response(raw)
    assert result.call_type == "BOOKING_LEAD"
    assert result.confidence == 0.9
    assert result.reason == "Asked about availability."


def test_parse_classification_response_clamps_out_of_range_confidence():
    raw = json.dumps({"call_type": "JUNK", "confidence": 5.0, "reason": "spam"})
    result = svc._parse_classification_response(raw)
    assert result.confidence == 1.0


def test_parse_classification_response_defaults_missing_confidence():
    raw = json.dumps({"call_type": "JUNK", "reason": "spam"})
    result = svc._parse_classification_response(raw)
    assert result.confidence == 0.5


def test_parse_classification_response_rejects_invalid_call_type():
    raw = json.dumps({"call_type": "NOT_A_REAL_TYPE", "confidence": 0.5, "reason": "x"})
    with pytest.raises(svc.CallClassificationError):
        svc._parse_classification_response(raw)


def test_parse_classification_response_rejects_non_json():
    with pytest.raises(svc.CallClassificationError):
        svc._parse_classification_response("not json at all")


async def test_classify_call_uses_llm_fallback_result(monkeypatch):
    monkeypatch.setattr(svc.settings, "llm_provider", "groq")
    monkeypatch.setattr(svc.settings, "groq_api_key", "test-key")

    async def _fake_call_llm_with_fallback(transcript):
        return json.dumps({"call_type": "GENERAL_QUERY", "confidence": 0.7, "reason": "Asked about house rules."})

    monkeypatch.setattr(svc, "_call_llm_with_fallback", _fake_call_llm_with_fallback)

    result = await svc.classify_call("Guest: What time is checkout?", duration_seconds=20.0)

    assert result.call_type == "GENERAL_QUERY"
    assert result.confidence == 0.7


async def test_classify_call_never_raises_degrades_to_unknown(monkeypatch):
    """The module's own contract: any failure at the LLM step must degrade
    to UNKNOWN rather than propagate, since on_pipeline_finished can never
    crash on this."""

    async def _raising_call_llm_with_fallback(transcript):
        raise RuntimeError("simulated provider outage")

    monkeypatch.setattr(svc, "_call_llm_with_fallback", _raising_call_llm_with_fallback)

    result = await svc.classify_call("Guest: Do you have anything free next weekend?", duration_seconds=20.0)

    assert result.call_type == "UNKNOWN"
    assert result.confidence == 0.0


async def test_classify_call_short_circuits_on_pre_check_without_calling_llm(monkeypatch):
    calls = []

    async def _tracking_call_llm_with_fallback(transcript):
        calls.append(transcript)
        return json.dumps({"call_type": "JUNK", "confidence": 1.0, "reason": "x"})

    monkeypatch.setattr(svc, "_call_llm_with_fallback", _tracking_call_llm_with_fallback)

    result = await svc.classify_call(None, duration_seconds=None)

    assert result.call_type == "INCOMPLETE"
    assert calls == []
