"""End-to-end wiring of per-call telemetry, exactly as a live call runs it:
real frames through the real processors in a real pipecat Pipeline (same
constructors/arguments as _run_pipeline_inner), then the same end-of-call
helper on_pipeline_finished calls (pipeline._persist_call_telemetry) into a
real test DB, then read back through the admin panel's aggregation. Proves
the chain processors -> ConversationQuality/usage rows -> call_quality_events
/ service_usage_events -> /admin payloads is connected end to end."""

import inspect
import re
from datetime import datetime, timedelta, timezone

import numpy as np
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InputAudioRawFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSTextFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData, TTSUsageMetricsData
from pipecat.pipeline.pipeline import Pipeline
from pipecat.tests.utils import run_test
from sqlalchemy import select

from app.config import settings
from app.models.call_quality_event import CallQualityEvent
from app.models.service_usage_event import ServiceUsageEvent
from app.services import admin_monitor_service as monitor
from app.voice import pipeline as voice_pipeline
from app.voice.audio_input_observer import AudioInputContext, AudioInputObserver, BotSpeechTap
from app.voice.call_metrics import CallMetricsCollector
from app.voice.conversation_quality import ConversationQuality, ValidationResult


def _call_frames():
    audio = np.full(160, 8000, dtype=np.int16).tobytes()  # 20 ms @ 8 kHz
    return [
        *[InputAudioRawFrame(audio=audio, sample_rate=8000, num_channels=1) for _ in range(50)],
        BotStartedSpeakingFrame(),
        TTSTextFrame("Namaste, aapke kitne guests hain?", aggregated_by="sentence"),
        MetricsFrame(data=[TTSUsageMetricsData(processor="SarvamTTSService#0", value=34)]),
        BotStoppedSpeakingFrame(),
        TranscriptionFrame(
            text="hum chaar log hain Goa mein",
            user_id="guest",
            timestamp="",
            result={"type": "data", "data": {"language_code": "hi-IN", "language_probability": 0.71}},
        ),
        UserStoppedSpeakingFrame(),
        MetricsFrame(
            data=[
                LLMUsageMetricsData(
                    processor="_FallbackGroqLLMService#0",
                    model=settings.groq_models[0],
                    value=LLMTokenUsage(prompt_tokens=1500, completion_tokens=60, total_tokens=1560),
                )
            ]
        ),
        FunctionCallInProgressFrame(function_name="get_pricing", tool_call_id="t1", arguments={}),
        FunctionCallResultFrame(function_name="get_pricing", tool_call_id="t1", arguments={}, result={"total": 9000}),
        BotStartedSpeakingFrame(),
    ]


async def test_live_call_telemetry_reaches_db_and_admin_panel(db_session, test_call_session, test_user):
    # Same construction as _run_pipeline_inner.
    context = AudioInputContext()
    quality = ConversationQuality()
    observer = AudioInputObserver(context, quality)
    tap = BotSpeechTap(context)
    metrics = CallMetricsCollector(
        groq_models=settings.groq_models,
        openrouter_model=settings.openrouter_model,
        tts_model=settings.sarvam_tts_model,
    )
    # A guard firing during the call, as a validator would record it.
    quality.record(ValidationResult(rule="response_shape", severity="WARNING", confidence=0.5))

    down, _ = await run_test(Pipeline([observer, tap, metrics]), frames_to_send=_call_frames())
    assert sum(isinstance(f, InputAudioRawFrame) for f in down) == 50  # observation only: nothing dropped
    assert [f.text for f in down if isinstance(f, TranscriptionFrame)] == ["hum chaar log hain Goa mein"]

    await voice_pipeline._persist_call_telemetry(
        db_session,
        call_session_id=test_call_session.id,
        host_user_id=test_user.id,
        conversation_quality=quality,
        audio_input_observer=observer,
        call_metrics=metrics,
        end_frame=EndFrame(),
        duration_seconds=42.0,
    )

    events = {
        e.rule: e
        for e in (
            await db_session.scalars(select(CallQualityEvent).where(CallQualityEvent.call_session_id == test_call_session.id))
        ).all()
    }
    assert set(events) == {"response_shape", "audio_input_telemetry", "call_metrics"}
    audio_summary = events["audio_input_telemetry"].metadata_json["summary"]
    assert audio_summary["segments_total"] == 1
    assert audio_summary["input_audio_seconds"] == 1.0
    assert audio_summary["lang_counts"] == {"hi-IN": 1}
    metrics_summary = events["call_metrics"].metadata_json["summary"]
    assert metrics_summary["tool_calls"] == {"get_pricing": 1}
    assert metrics_summary["price_quoted"] is True
    assert metrics_summary["end_frame"] == "EndFrame"
    assert len(metrics_summary["response_latency_s"]) == 1

    usage = {
        (u.service, u.unit): u
        for u in (
            await db_session.scalars(select(ServiceUsageEvent).where(ServiceUsageEvent.call_session_id == test_call_session.id))
        ).all()
    }
    assert usage[("groq", "prompt_tokens")].quantity == 1500
    assert usage[("groq", "prompt_tokens")].model == settings.groq_models[0]
    assert usage[("sarvam_tts", "characters")].quantity == 34
    # STT metered on audio actually streamed (1.0 s), not the 42 s call duration.
    assert usage[("sarvam_stt", "audio_seconds")].quantity == 1.0
    assert all(u.user_id == test_user.id for u in usage.values())

    # And the admin panel reads it back.
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    scope = monitor.Scope(since=today, until=today + timedelta(days=1))
    perf = await monitor.performance(db_session, scope)
    assert perf["coverage"]["calls_with_call_metrics"] == 1
    assert perf["tools"][0]["name"] == "get_pricing"
    aud = await monitor.audio(db_session, scope)
    assert aud["coverage"]["calls_with_audio_telemetry"] == 1
    use = await monitor.usage(db_session, scope)
    assert {"groq", "sarvam"} <= {a["account"] for a in use["by_account"]}
    hosts = await monitor.hosts(db_session, scope)
    assert hosts["hosts"][0]["cost_inr"] > 0


def test_processors_sit_where_the_design_requires_in_the_live_pipeline():
    """Placement is load-bearing: the observer must be directly after stt
    (raw transcript + interruption-direction attribution) and the tap +
    collector directly after tts (spoken text + TTS metrics)."""
    source = inspect.getsource(voice_pipeline._run_pipeline_inner)
    assert re.search(r"stt,\s*\*telemetry_in,\s*low_confidence_transcript_guard,", source)
    assert re.search(r"tts,\s*\*telemetry_out,\s*transport\.output\(\),", source)
    assert "usage_meter.bind_usage_context(call_session_id, host_user_id)" in source
    assert "await _persist_call_telemetry(" in source
