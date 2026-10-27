"""Per-call usage + performance telemetry for the internal admin panel --
OBSERVATION ONLY, same contract as app/voice/audio_input_observer.py: every
frame is forwarded unchanged and every analysis step fails open.

CallMetricsCollector sits directly after `tts` (next to BotSpeechTap). From
that one position it sees everything it needs without a pipeline-wide
observer (which would be handed every audio frame at every hop):
- MetricsFrame from the LLM (token usage, TTFB -- flows down through the
  guards) and from TTS (character usage, TTFB).
- FunctionCallInProgressFrame / FunctionCallResultFrame -- which tools ran,
  how long they took, whether they returned an error.
- UserStoppedSpeakingFrame (broadcast downstream by the user aggregator when
  a guest turn closes) and BotStartedSpeakingFrame (broadcast upstream by
  the output transport) -- the response latency the guest actually waits
  through after their turn is closed.
- ErrorFrame passing through this position.

pipecat already computes exact token/character counts (enable_usage_metrics
in PipelineParams) but until now they only went to logs. At call end,
finalize() returns:
1. one ValidationResult (rule=CALL_METRICS_RULE, internal -- excluded from
   host analytics) with the performance summary, persisted via the existing
   record_quality_events path; and
2. usage rows (LLM tokens per provider/model, Sarvam TTS characters, Sarvam
   STT audio seconds) for app/services/usage_meter.record_call_usage.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    ErrorFrame,
    Frame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    MetricsFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import LLMUsageMetricsData, TTFBMetricsData, TTSUsageMetricsData
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.models.call_quality_event import CALL_METRICS_RULE
from app.voice.conversation_quality import ValidationResult

# Per-call cap on raw latency samples kept for percentile computation
# across calls; counts/totals stay exact past it.
MAX_SAMPLES = 100
# A response that hasn't started within this long after the guest's turn
# closed is not a "response" to that turn (e.g. Mira stayed silent and the
# silence watchdog nudged much later) -- excluded from latency.
MAX_RESPONSE_LATENCY_SECONDS = 30.0

PRICE_TOOLS = frozenset({"get_pricing", "negotiate_rate"})
ENGAGEMENT_TOOLS = frozenset({"get_pricing", "negotiate_rate", "check_calendar"})


@dataclass(frozen=True)
class UsageRow:
    service: str
    unit: str
    quantity: float
    model: str | None = None


def _provider_for(processor: str, model: str | None, groq_models: list[str], openrouter_model: str) -> str:
    """Bills tokens to the provider that actually served them. Model id is
    checked first because _FallbackGroqLLMService's processor name stays the
    same whichever Groq model it fell back to."""
    if model and model in groq_models:
        return "groq"
    if model and model == openrouter_model:
        return "openrouter"
    name = processor.lower()
    for provider in ("groq", "openrouter", "anthropic"):
        if provider in name:
            return provider
    return "llm_other"


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))]


def _is_error_result(result) -> bool:
    if isinstance(result, dict):
        status = str(result.get("status", "")).lower()
        return bool(result.get("error")) or status in ("error", "failed", "failure")
    if isinstance(result, str):
        lowered = result.lower()
        return lowered.startswith("error") or '"error"' in lowered
    return False


class CallMetricsCollector(FrameProcessor):
    def __init__(
        self,
        *,
        groq_models: list[str],
        openrouter_model: str,
        tts_model: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        super().__init__()
        self._groq_models = list(groq_models)
        self._openrouter_model = openrouter_model
        self._tts_model = tts_model
        # NOT self._clock -- FrameProcessor sets its own _clock (a pipecat
        # SystemClock) when the pipeline starts, which silently replaced it.
        self._monotonic = clock
        self._finalized = False

        # (provider, model) -> Counter(prompt_tokens=..., completion_tokens=..., completions=...)
        self._llm: dict[tuple[str, str | None], Counter] = defaultdict(Counter)
        self._tts_characters = 0
        self._ttfb: dict[str, list[float]] = defaultdict(list)
        self._tool_calls: Counter = Counter()
        self._tool_errors: Counter = Counter()
        self._tool_durations: dict[str, list[float]] = defaultdict(list)
        self._tool_started: dict[str, tuple[str, float]] = {}
        self._tool_seen_results: set[str] = set()
        self._response_latencies: list[float] = []
        self._user_turns = 0
        self._user_stopped_at: float | None = None
        self._errors = 0
        self._processing_seconds = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        started = time.perf_counter()
        try:
            self._observe(frame)
        except Exception:
            logger.exception("call_metrics_collector_failed frame={}", type(frame).__name__)
        self._processing_seconds += time.perf_counter() - started
        await self.push_frame(frame, direction)

    def _observe(self, frame: Frame) -> None:
        if isinstance(frame, MetricsFrame):
            for data in frame.data:
                self._observe_metric(data)
        elif isinstance(frame, FunctionCallInProgressFrame):
            if frame.tool_call_id not in self._tool_started:
                self._tool_started[frame.tool_call_id] = (frame.function_name, self._monotonic())
                self._tool_calls[frame.function_name] += 1
        elif isinstance(frame, FunctionCallResultFrame):
            if frame.tool_call_id in self._tool_seen_results:
                return
            self._tool_seen_results.add(frame.tool_call_id)
            name = frame.function_name
            started = self._tool_started.get(frame.tool_call_id)
            if started is not None and len(self._tool_durations[name]) < MAX_SAMPLES:
                self._tool_durations[name].append(self._monotonic() - started[1])
            if _is_error_result(frame.result):
                self._tool_errors[name] += 1
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._user_turns += 1
            self._user_stopped_at = self._monotonic()
        elif isinstance(frame, BotStartedSpeakingFrame):
            if self._user_stopped_at is not None:
                latency = self._monotonic() - self._user_stopped_at
                if 0 <= latency <= MAX_RESPONSE_LATENCY_SECONDS and len(self._response_latencies) < MAX_SAMPLES:
                    self._response_latencies.append(latency)
                self._user_stopped_at = None
        elif isinstance(frame, ErrorFrame):
            self._errors += 1

    def _observe_metric(self, data) -> None:
        if isinstance(data, LLMUsageMetricsData):
            provider = _provider_for(data.processor, data.model, self._groq_models, self._openrouter_model)
            bucket = self._llm[(provider, data.model)]
            bucket["prompt_tokens"] += data.value.prompt_tokens or 0
            bucket["completion_tokens"] += data.value.completion_tokens or 0
            bucket["cached_tokens"] += data.value.cache_read_input_tokens or 0
            bucket["completions"] += 1
        elif isinstance(data, TTSUsageMetricsData):
            self._tts_characters += int(data.value or 0)
        elif isinstance(data, TTFBMetricsData):
            name = data.processor.lower()
            kind = "tts" if "tts" in name else "stt" if "stt" in name else "llm" if "llm" in name else "other"
            if data.value and data.value > 0 and len(self._ttfb[kind]) < MAX_SAMPLES:
                self._ttfb[kind].append(float(data.value))

    @property
    def tool_calls(self) -> Counter:
        return self._tool_calls

    def finalize(
        self,
        *,
        end_frame: str | None,
        end_reason: str | None,
        stt_audio_seconds: float | None,
        stt_model: str | None,
    ) -> tuple[ValidationResult | None, list[UsageRow]]:
        """Idempotent; never raises. Returns (telemetry record, usage rows)."""
        if self._finalized:
            return None, []
        self._finalized = True
        try:
            usage: list[UsageRow] = []
            for (provider, model), counts in sorted(self._llm.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
                if counts["prompt_tokens"]:
                    usage.append(UsageRow(provider, "prompt_tokens", counts["prompt_tokens"], model))
                if counts["completion_tokens"]:
                    usage.append(UsageRow(provider, "completion_tokens", counts["completion_tokens"], model))
            if self._tts_characters:
                usage.append(UsageRow("sarvam_tts", "characters", self._tts_characters, self._tts_model))
            if stt_audio_seconds:
                usage.append(UsageRow("sarvam_stt", "audio_seconds", round(stt_audio_seconds, 2), stt_model))

            summary = {
                "end_frame": end_frame,
                "end_reason": end_reason,
                "user_turns": self._user_turns,
                "errors": self._errors,
                "tts_characters": self._tts_characters,
                "llm": [
                    {"provider": provider, "model": model, **dict(counts)}
                    for (provider, model), counts in self._llm.items()
                ],
                "tool_calls": dict(self._tool_calls),
                "tool_errors": dict(self._tool_errors),
                "tool_durations_s": {k: [round(v, 3) for v in vs] for k, vs in self._tool_durations.items()},
                "response_latency_s": [round(v, 3) for v in self._response_latencies],
                "response_latency_p50_s": _percentile(self._response_latencies, 50),
                "ttfb_s": {k: [round(v, 3) for v in vs] for k, vs in self._ttfb.items()},
                "price_quoted": bool(PRICE_TOOLS & set(self._tool_calls)),
                "negotiated": bool(self._tool_calls.get("negotiate_rate")),
                "engaged_pricing_tools": bool(ENGAGEMENT_TOOLS & set(self._tool_calls)),
                "collector_overhead_ms": round(self._processing_seconds * 1000.0, 1),
            }
            record = ValidationResult(
                rule=CALL_METRICS_RULE,
                severity="INFO",
                confidence=1.0,
                turn_index=self._user_turns,
                processing_time_ms=self._processing_seconds * 1000.0,
                metadata={"summary": summary},
            )
            return record, usage
        except Exception:
            logger.exception("call_metrics_finalize_failed")
            return None, []
