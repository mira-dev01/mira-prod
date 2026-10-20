"""Covers app/voice/call_metrics.py: observation-only per-call usage +
performance collection for the admin panel."""

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    ErrorFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    MetricsFrame,
    TTSTextFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData, TTFBMetricsData, TTSUsageMetricsData
from pipecat.tests.utils import run_test

from app.models.call_quality_event import CALL_METRICS_RULE
from app.voice.call_metrics import CallMetricsCollector

GROQ_MODELS = ["openai/gpt-oss-120b", "llama-3.1-8b-instant"]


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _collector(clock=None):
    return CallMetricsCollector(
        groq_models=GROQ_MODELS, openrouter_model="openai/gpt-4.1", tts_model="bulbul:v3", clock=clock or FakeClock()
    )


def _llm_usage(model, prompt, completion, processor="_FallbackGroqLLMService#0"):
    return MetricsFrame(
        data=[
            LLMUsageMetricsData(
                processor=processor,
                model=model,
                value=LLMTokenUsage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion),
            )
        ]
    )


@pytest.mark.asyncio
async def test_frames_pass_through_unchanged():
    collector = _collector()
    frames = [_llm_usage("openai/gpt-oss-120b", 10, 5), TTSTextFrame("Namaste", aggregated_by="sentence")]
    down, _ = await run_test(collector, frames_to_send=frames)
    assert any(isinstance(f, MetricsFrame) for f in down)
    assert any(isinstance(f, TTSTextFrame) for f in down)


def test_tokens_billed_to_provider_by_model_including_fallback():
    c = _collector()
    c._observe(_llm_usage("openai/gpt-oss-120b", 1000, 100))
    c._observe(_llm_usage("llama-3.1-8b-instant", 500, 50))  # Groq fallback model, same processor
    c._observe(_llm_usage("openai/gpt-4.1", 300, 30, processor="OpenRouterLLMService#0"))
    c._observe(MetricsFrame(data=[TTSUsageMetricsData(processor="SarvamTTSService#0", value=420)]))

    record, usage = c.finalize(end_frame="EndFrame", end_reason=None, stt_audio_seconds=61.5, stt_model="saaras:v3")

    rows = {(u.service, u.unit, u.model): u.quantity for u in usage}
    assert rows[("groq", "prompt_tokens", "openai/gpt-oss-120b")] == 1000
    assert rows[("groq", "completion_tokens", "llama-3.1-8b-instant")] == 50
    assert rows[("openrouter", "prompt_tokens", "openai/gpt-4.1")] == 300
    assert rows[("sarvam_tts", "characters", "bulbul:v3")] == 420
    assert rows[("sarvam_stt", "audio_seconds", "saaras:v3")] == 61.5
    assert record.rule == CALL_METRICS_RULE


def test_tool_calls_durations_errors_and_flags():
    clock = FakeClock()
    c = _collector(clock)
    c._observe(FunctionCallInProgressFrame(function_name="get_pricing", tool_call_id="a", arguments={}))
    clock.now += 1.5
    c._observe(FunctionCallResultFrame(function_name="get_pricing", tool_call_id="a", arguments={}, result={"total": 5000}))
    c._observe(FunctionCallInProgressFrame(function_name="check_calendar", tool_call_id="b", arguments={}))
    c._observe(FunctionCallResultFrame(function_name="check_calendar", tool_call_id="b", arguments={}, result={"error": "x"}))
    # Duplicate result frame for the same call id is ignored.
    c._observe(FunctionCallResultFrame(function_name="check_calendar", tool_call_id="b", arguments={}, result={"error": "x"}))

    summary = c.finalize(end_frame="EndFrame", end_reason=None, stt_audio_seconds=None, stt_model=None)[0].metadata["summary"]
    assert summary["tool_calls"] == {"get_pricing": 1, "check_calendar": 1}
    assert summary["tool_errors"] == {"check_calendar": 1}
    assert summary["tool_durations_s"]["get_pricing"] == [1.5]
    assert summary["price_quoted"] is True
    assert summary["negotiated"] is False
    assert summary["engaged_pricing_tools"] is True


def test_response_latency_and_ttfb_and_errors():
    clock = FakeClock()
    c = _collector(clock)
    c._observe(BotStartedSpeakingFrame())  # greeting: no preceding user turn -> no sample
    c._observe(UserStoppedSpeakingFrame())
    clock.now += 1.2
    c._observe(BotStartedSpeakingFrame())
    c._observe(MetricsFrame(data=[TTFBMetricsData(processor="_FallbackGroqLLMService#0", value=0.4)]))
    c._observe(MetricsFrame(data=[TTFBMetricsData(processor="SarvamTTSService#0", value=0.25)]))
    c._observe(ErrorFrame(error="boom"))

    summary = c.finalize(end_frame="CancelFrame", end_reason=None, stt_audio_seconds=None, stt_model=None)[0].metadata[
        "summary"
    ]
    assert summary["response_latency_s"] == [1.2]
    assert summary["user_turns"] == 1
    assert summary["ttfb_s"] == {"llm": [0.4], "tts": [0.25]}
    assert summary["errors"] == 1
    assert summary["end_frame"] == "CancelFrame"


def test_finalize_is_idempotent_and_skips_zero_usage():
    c = _collector()
    record, usage = c.finalize(end_frame=None, end_reason=None, stt_audio_seconds=0, stt_model=None)
    assert record is not None and usage == []
    assert c.finalize(end_frame=None, end_reason=None, stt_audio_seconds=0, stt_model=None) == (None, [])
