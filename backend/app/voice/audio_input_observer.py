"""Phase 1A audio-input telemetry -- OBSERVATION ONLY.

Nothing in this module changes what Mira hears, says, or when she says it:
every frame is forwarded unchanged, in order, and every analysis step is
wrapped so a bug here can only lose telemetry, never break a live call
(same fail-open discipline as every other optional signal in this pipeline).

Why it exists: three failure modes show up in live call logs -- background
audio promoted to speech, real speech mis-transcribed, and the turn
detector reacting to bad audio -- and every threshold that touches them
(_VAD_PARAMS, config.py's sarvam_vad_*, the 0.4 language_probability cutoff)
is explicitly marked "unvalidated" in its own comment because there was no
real-call data to tune against. This module collects exactly that data, plus
a SHADOW verdict for each candidate Phase 1B/3 rule ("what would this rule
have done to this segment?") without ever acting on it:

- echo:  a transcript that closely matches what Mira herself just said,
         arriving while/just after she spoke (speakerphone echo).
- short: a 1-word segment overlapping Mira's speech that isn't an explicit
         stop/interrupt word (TV fragments, backchannels, coughs).
- level: a segment far quieter than this caller's own established speech
         level (a nearby person / TV on a speakerphone call).

Plus barge-in attribution: which component actually broadcast each
interruption that cut Mira off (Sarvam STT's server-side VAD vs. pipecat's
local turn controller) and whether a real transcript ever followed it.

Three pieces, one per-call lifetime (same as ConversationState):
- AudioInputContext: shared per-call audio-layer facts (is Mira speaking,
  what she recently said, Sarvam VAD event counts). Deliberately NOT part of
  ConversationState -- these are audio facts, not conversation facts.
- BotSpeechTap: passthrough placed after `tts`; records the text Mira
  actually spoke (TTSTextFrame only flows downstream, so the input side can't
  otherwise see it).
- AudioInputObserver: passthrough placed right after `stt`; measures input
  levels, analyses each transcript, attributes interruptions, and at call end
  writes ONE ValidationResult (rule=AUDIO_INPUT_TELEMETRY_RULE) into
  ConversationQuality, persisted by the existing record_quality_events path.

Write-only into ConversationQuality: nothing reads this telemetry back into
a live call, so it adds no quality->behavior bridge (CLAUDE.md invariant).
Guest content: only segments of <=2 words keep their text (needed to decide
the Phase 1B stop-word allow-list); longer transcripts are stored as counts
only, matching low_confidence_transcript_guard's "log the score, not the
guest's words" discipline.
"""

from __future__ import annotations

import math
import time
import unicodedata
from collections import Counter, deque
from collections.abc import Callable

import numpy as np
from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.models.call_quality_event import AUDIO_INPUT_TELEMETRY_RULE
from app.voice.conversation_quality import ConversationQuality, ValidationResult
from app.voice.low_confidence_transcript_guard import DEFAULT_LANGUAGE_PROBABILITY_THRESHOLD

TELEMETRY_VERSION = 1

# Shadow-rule parameters. Starting points for SHADOW evaluation only -- the
# point of collecting this telemetry is to replace these with measured values
# before any of them is ever enforced (Phase 1B/3).
ECHO_LOOKBACK_SECONDS = 8.0
ECHO_SIMILARITY_THRESHOLD = 0.8
BOT_OVERLAP_TAIL_SECONDS = 0.5
LEVEL_GATE_RELATIVE_DB = -12.0
BASELINE_MIN_SEGMENTS = 2
BASELINE_MAX_SEGMENTS = 10
# A barge-in with no transcript within this window (or before Mira starts
# speaking again) is counted as a phantom -- Mira was cut off by something
# that never turned into words.
PHANTOM_WINDOW_SECONDS = 8.0
# One guest speech-start can produce TWO InterruptionFrames when Sarvam's
# server-side VAD events are active: one broadcast by stt on START_SPEECH and
# one by the user aggregator's turn controller. A second interruption from
# the OTHER source inside this window is the same barge-in, not a new one.
DUAL_SOURCE_WINDOW_SECONDS = 1.5
# pipecat's default TranscriptionUserTurnStartStrategy can start a user turn
# (and so broadcast the interruption) from a transcript that arrived while
# Mira was speaking -- in that case the InterruptionFrame reaches this
# observer just AFTER the transcript that caused it. An interruption within
# this window of a transcript is attributed to that transcript.
TRANSCRIPT_TRIGGER_WINDOW_SECONDS = 1.0
# Fallback segment window when Sarvam's response carries no usable
# audio_duration.
FALLBACK_SEGMENT_SECONDS = 4.0
MAX_SEGMENT_SECONDS = 30.0
AUDIO_HISTORY_SECONDS = 30.0

# Per-call caps so one long/noisy call can't produce an unbounded JSONB row.
# Summary counters stay exact past the cap; only the per-item lists stop.
MAX_SEGMENT_RECORDS = 150
MAX_BARGE_IN_RECORDS = 100

SILENCE_DBFS = -100.0

# Words that must always be allowed to interrupt Mira (the would-be Phase 1B
# short-segment rule never drops these). Romanized + Devanagari, since
# Sarvam's codemix mode emits either.
STOP_WORDS = frozenset(
    {
        "stop", "wait", "hold", "hello", "hey", "excuse", "sorry", "no", "nahi", "nahin", "nai",
        "ruko", "ruk", "rukiye", "suno", "suniye", "listen",
        "रुको", "रुकिए", "रुकिये", "नहीं", "नही", "हेलो", "हैलो", "सुनो", "सुनिए", "सुनिये", "सॉरी",
    }
)
BACKCHANNELS = frozenset(
    {
        "ok", "okay", "okey", "haan", "han", "ha", "hmm", "hm", "hmmm", "yes", "ya", "yeah", "yep",
        "ji", "achha", "acha", "accha", "theek", "thik", "sure", "right", "fine", "alright",
        "हाँ", "हां", "हा", "जी", "अच्छा", "ठीक", "ओके", "हम्म", "हम",
    }
)


def _normalize(text: str) -> str:
    """Lowercase, drop punctuation/symbols, collapse whitespace. Unicode-
    category based (not a regex \\w class) so Devanagari vowel signs, which
    are combining marks rather than letters, survive intact."""
    kept = [
        " " if unicodedata.category(ch)[0] in ("P", "S") else ch
        for ch in text.lower()
    ]
    return " ".join("".join(kept).split())


def _token_class(tokens: list[str]) -> str:
    if any(t in STOP_WORDS for t in tokens):
        return "stop_word"
    if tokens and all(t.isdigit() for t in tokens):
        return "number"
    if tokens and all(t in BACKCHANNELS for t in tokens):
        return "backchannel"
    return "other"


def _frame_dbfs(audio: bytes) -> float:
    """RMS level of one 16-bit PCM frame in dBFS (0 = full scale)."""
    if len(audio) < 2:
        return SILENCE_DBFS
    samples = np.frombuffer(audio[: len(audio) - (len(audio) % 2)], dtype=np.int16).astype(np.float32)
    rms = float(np.sqrt(np.mean(samples * samples)))
    if rms <= 0.0:
        return SILENCE_DBFS
    return max(SILENCE_DBFS, 20.0 * math.log10(rms / 32768.0))


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[index]


def _histogram_percentile(histogram: Counter, pct: float) -> float | None:
    total = sum(histogram.values())
    if total == 0:
        return None
    target = (pct / 100.0) * total
    running = 0
    for level in sorted(histogram):
        running += histogram[level]
        if running >= target:
            return float(level)
    return float(max(histogram))


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


class AudioInputContext:
    """Per-call audio-layer facts shared between BotSpeechTap (writer of
    bot speech text), the Sarvam STT subclass (writer of VAD event counts),
    and AudioInputObserver (reader + writer of bot speaking intervals).
    Never shared across calls."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.bot_speaking = False
        # (start, end) -- end is None while Mira is still speaking.
        self._bot_intervals: deque[list[float | None]] = deque(maxlen=64)
        self._bot_text: deque[tuple[float, str]] = deque(maxlen=200)
        self.sarvam_vad_events: Counter = Counter()
        self.last_sarvam_start_speech_at: float | None = None

    def bot_started(self, now: float) -> None:
        if not self.bot_speaking:
            self.bot_speaking = True
            self._bot_intervals.append([now, None])

    def bot_stopped(self, now: float) -> None:
        if self.bot_speaking:
            self.bot_speaking = False
            if self._bot_intervals and self._bot_intervals[-1][1] is None:
                self._bot_intervals[-1][1] = now

    def bot_spoke_during(self, start: float, end: float, tail: float = BOT_OVERLAP_TAIL_SECONDS) -> bool:
        for interval_start, interval_end in self._bot_intervals:
            effective_end = (interval_end if interval_end is not None else end) + tail
            if interval_start <= end and effective_end >= start:
                return True
        return False

    def bot_speaking_seconds(self, now: float) -> float:
        return sum(((e if e is not None else now) - s) for s, e in self._bot_intervals)

    def record_bot_text(self, text: str) -> None:
        if text and text.strip():
            self._bot_text.append((self.clock(), text))

    def recent_bot_text(self, since: float) -> str:
        return " ".join(text for t, text in self._bot_text if t >= since)

    def note_sarvam_vad_event(self, signal: str | None) -> None:
        signal = signal or "unknown"
        if not self.sarvam_vad_events:
            # INFO, once per call: Sarvam's own per-event log line is DEBUG
            # (invisible in production logs), so this is the only production-
            # visible proof of whether Sarvam's server-side VAD is active.
            logger.info("sarvam_vad_events_active first_signal={}", signal)
        self.sarvam_vad_events[signal] += 1
        if signal == "START_SPEECH":
            self.last_sarvam_start_speech_at = self.clock()


class BotSpeechTap(FrameProcessor):
    """Passthrough after `tts`: records the text Mira actually spoke."""

    def __init__(self, context: AudioInputContext):
        super().__init__()
        self._context = context

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSTextFrame) and direction == FrameDirection.DOWNSTREAM:
            try:
                self._context.record_bot_text(frame.text)
            except Exception:
                logger.exception("bot_speech_tap_failed")
        await self.push_frame(frame, direction)


class AudioInputObserver(FrameProcessor):
    """Passthrough right after `stt`. See module docstring."""

    def __init__(self, context: AudioInputContext, quality: ConversationQuality):
        super().__init__()
        self._context = context
        self._quality = quality
        # NOT self._clock -- FrameProcessor sets its own _clock (a pipecat
        # SystemClock) when the pipeline starts, which silently replaced it.
        self._monotonic = context.clock
        self._started_at = self._monotonic()
        self._finalized = False

        self._audio_history: deque[tuple[float, float]] = deque()
        self._level_histogram: Counter = Counter()
        self._input_audio_seconds = 0.0
        self._input_sample_rate: int | None = None
        self._last_transcript_at: float | None = None

        self._baseline_levels: deque[float] = deque(maxlen=BASELINE_MAX_SEGMENTS)
        self._segments: list[dict] = []
        self._barge_ins: list[dict] = []
        self._pending_barge_in: dict | None = None
        # (time, source, was_barge_in) of the most recent interruption, for
        # dual-source de-duplication (see DUAL_SOURCE_WINDOW_SECONDS).
        self._last_interruption: tuple[float, str, bool] | None = None
        self._last_segment_suspect = False
        self._last_lang: str | None = None
        self._lang_counts: Counter = Counter()
        self._counters: Counter = Counter()
        self._language_probabilities: list[float] = []
        self._processing_seconds = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        started = time.perf_counter()
        try:
            self._observe(frame, direction)
        except Exception:
            logger.exception("audio_input_observer_failed frame={}", type(frame).__name__)
        self._processing_seconds += time.perf_counter() - started
        await self.push_frame(frame, direction)

    # -- observation ---------------------------------------------------------

    def _observe(self, frame: Frame, direction: FrameDirection) -> None:
        now = self._monotonic()
        if isinstance(frame, InputAudioRawFrame):
            self._observe_audio(frame, now)
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._context.bot_started(now)
            if self._pending_barge_in is not None:
                self._resolve_barge_in("no_transcript_bot_resumed")
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._context.bot_stopped(now)
        elif isinstance(frame, InterruptionFrame):
            self._observe_interruption(direction, now)
        elif isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            if frame.text and frame.text.strip():
                self._observe_transcript(frame, now)

    def _observe_audio(self, frame: InputAudioRawFrame, now: float) -> None:
        if self._input_sample_rate is None:
            self._input_sample_rate = frame.sample_rate
        channels = max(1, frame.num_channels or 1)
        if frame.sample_rate:
            self._input_audio_seconds += len(frame.audio) / (2 * channels * frame.sample_rate)
        level = _frame_dbfs(frame.audio)
        self._level_histogram[int(round(level))] += 1
        self._audio_history.append((now, level))
        cutoff = now - AUDIO_HISTORY_SECONDS
        while self._audio_history and self._audio_history[0][0] < cutoff:
            self._audio_history.popleft()
        if self._pending_barge_in is not None and now - self._pending_barge_in["_at"] > PHANTOM_WINDOW_SECONDS:
            self._resolve_barge_in("no_transcript")

    def _observe_interruption(self, direction: FrameDirection, now: float) -> None:
        # The observer sits directly after `stt`: a DOWNSTREAM interruption
        # can only have been broadcast by stt itself (Sarvam's server-side
        # START_SPEECH path); an UPSTREAM one came from a processor below us
        # -- in practice the user aggregator's turn controller (local VAD).
        source = "sarvam_stt" if direction == FrameDirection.DOWNSTREAM else "downstream_turn_controller"
        bot_was_speaking = self._context.bot_speaking or self._context.bot_spoke_during(now, now)
        last = self._last_interruption
        if last is not None and now - last[0] <= DUAL_SOURCE_WINDOW_SECONDS and source != last[1]:
            # Same speech event, second broadcaster -- record that both
            # sources fired, never count it as a second interruption.
            self._last_interruption = None
            self._counters["interruptions_dual_source"] += 1
            if last[2]:
                self._counters["barge_ins_dual_source"] += 1
                if self._pending_barge_in is not None:
                    self._pending_barge_in["also_from"] = source
                elif self._barge_ins and self._barge_ins[-1].get("also_from") is None:
                    self._barge_ins[-1]["also_from"] = source
            return
        self._last_interruption = (now, source, bot_was_speaking)
        if not bot_was_speaking:
            self._counters[f"interruptions_bot_silent_{source}"] += 1
            return
        if self._pending_barge_in is not None:
            self._resolve_barge_in("no_transcript_superseded")
        self._counters["barge_ins_total"] += 1
        self._counters[f"barge_ins_{source}"] += 1
        sarvam_start = self._context.last_sarvam_start_speech_at
        transcript_triggered = (
            self._last_transcript_at is not None and now - self._last_transcript_at <= TRANSCRIPT_TRIGGER_WINDOW_SECONDS
        )
        self._pending_barge_in = {
            "_at": now,
            "t": _r(now - self._started_at),
            "source": source,
            "trigger": "transcript" if transcript_triggered else "speech_start",
            "also_from": None,
            "sarvam_start_speech_within_1s": sarvam_start is not None and 0 <= now - sarvam_start <= 1.0,
        }
        if transcript_triggered:
            # The words that caused this barge-in already arrived -- resolve
            # it against that transcript instead of waiting for another.
            self._counters["barge_ins_transcript_triggered"] += 1
            self._resolve_barge_in(
                "transcript_suspect" if self._last_segment_suspect else "transcript",
                transcript_delay=self._last_transcript_at - now,
            )

    def _resolve_barge_in(self, outcome: str, transcript_delay: float | None = None) -> None:
        pending = self._pending_barge_in
        self._pending_barge_in = None
        if pending is None:
            return
        self._counters[f"barge_in_outcome_{outcome}"] += 1
        if outcome != "transcript":
            self._counters["phantom_barge_ins"] += 1
        if len(self._barge_ins) < MAX_BARGE_IN_RECORDS:
            record = {k: v for k, v in pending.items() if not k.startswith("_")}
            record["outcome"] = outcome
            record["transcript_delay_s"] = _r(transcript_delay)
            self._barge_ins.append(record)
        else:
            self._counters["records_truncated"] += 1

    def _observe_transcript(self, frame: TranscriptionFrame, now: float) -> None:
        text = frame.text.strip()
        normalized = _normalize(text)
        tokens = normalized.split()
        word_count = len(tokens)

        data = frame.result.get("data") if isinstance(frame.result, dict) else None
        data = data if isinstance(data, dict) else {}
        probability = data.get("language_probability")
        probability = float(probability) if isinstance(probability, (int, float)) else None
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        audio_duration = metrics.get("audio_duration")
        audio_duration = float(audio_duration) if isinstance(audio_duration, (int, float)) else None
        processing_latency = metrics.get("processing_latency")
        processing_latency = float(processing_latency) if isinstance(processing_latency, (int, float)) else None

        # Estimate the window of input audio this transcript came from.
        if audio_duration is not None and 0 < audio_duration <= MAX_SEGMENT_SECONDS:
            segment_start = now - audio_duration - (processing_latency or 0.0)
        elif self._last_transcript_at is not None:
            segment_start = max(self._last_transcript_at, now - MAX_SEGMENT_SECONDS)
        else:
            segment_start = now - FALLBACK_SEGMENT_SECONDS
        self._last_transcript_at = now

        segment_levels = [level for t, level in self._audio_history if t >= segment_start]
        segment_p90 = _percentile(segment_levels, 90)
        baseline = (
            _percentile(list(self._baseline_levels), 50)
            if len(self._baseline_levels) >= BASELINE_MIN_SEGMENTS
            else None
        )
        relative_db = segment_p90 - baseline if (segment_p90 is not None and baseline is not None) else None

        bot_overlap = self._context.bot_speaking or self._context.bot_spoke_during(segment_start, now)
        guard_would_trigger = probability is not None and probability < DEFAULT_LANGUAGE_PROBABILITY_THRESHOLD
        token_class = _token_class(tokens) if word_count <= 2 else None

        echo_score = 0.0
        if bot_overlap and tokens:
            bot_tokens = set(_normalize(self._context.recent_bot_text(segment_start - ECHO_LOOKBACK_SECONDS)).split())
            if bot_tokens:
                echo_score = sum(1 for t in tokens if t in bot_tokens) / word_count

        shadow_echo = bot_overlap and word_count >= 2 and echo_score >= ECHO_SIMILARITY_THRESHOLD
        shadow_short = bot_overlap and word_count == 1 and token_class != "stop_word"
        shadow_level = None
        if relative_db is not None and relative_db <= LEVEL_GATE_RELATIVE_DB:
            shadow_level = "drop" if word_count <= 2 else "clarify"

        # Only clean, confidently-transcribed, multi-word segments spoken
        # while Mira was silent establish what "this caller's voice" sounds
        # like -- updated AFTER this segment's own comparison.
        if segment_p90 is not None and not bot_overlap and word_count >= 3 and not guard_would_trigger:
            self._baseline_levels.append(segment_p90)

        self._counters["segments_total"] += 1
        self._counters["words_total"] += word_count
        if word_count <= 2:
            self._counters["short_segments"] += 1
        if bot_overlap:
            self._counters["segments_bot_overlap"] += 1
        if guard_would_trigger:
            self._counters["guard_triggers"] += 1
            if word_count <= 2:
                self._counters["guard_triggers_short"] += 1
        if shadow_echo:
            self._counters["shadow_echo_drop"] += 1
        if shadow_short:
            self._counters["shadow_short_drop"] += 1
            self._counters[f"shadow_short_drop_{token_class}"] += 1
        if shadow_level:
            self._counters[f"shadow_level_{shadow_level}"] += 1
        if shadow_echo or shadow_short or shadow_level:
            self._counters["segments_any_shadow_action"] += 1
        if probability is not None:
            self._language_probabilities.append(probability)
        self._last_segment_suspect = bool(shadow_echo or shadow_short or shadow_level == "drop")

        # Language mix -- only multi-word segments, since a 1-word "okay"
        # carries almost no real language signal (see guard docstring).
        lang = data.get("language_code")
        if isinstance(lang, str) and lang and word_count >= 2:
            self._lang_counts[lang] += 1
            if self._last_lang is not None and lang != self._last_lang:
                self._counters["lang_switches"] += 1
            self._last_lang = lang

        if self._pending_barge_in is not None:
            delay = now - self._pending_barge_in["_at"]
            if delay <= PHANTOM_WINDOW_SECONDS:
                outcome = "transcript_suspect" if (shadow_echo or shadow_short or shadow_level == "drop") else "transcript"
                self._resolve_barge_in(outcome, transcript_delay=delay)
            else:
                self._resolve_barge_in("no_transcript")

        if len(self._segments) >= MAX_SEGMENT_RECORDS:
            self._counters["records_truncated"] += 1
            return
        self._segments.append(
            {
                "i": self._counters["segments_total"],
                "t": _r(now - self._started_at),
                "words": word_count,
                "chars": len(text),
                # Text kept ONLY for <=2-word segments -- see module docstring.
                "short_text": text if word_count <= 2 else None,
                "token_class": token_class,
                "lang": data.get("language_code"),
                "lang_prob": _r(probability, 3),
                "audio_duration_s": _r(audio_duration),
                "stt_latency_s": _r(processing_latency),
                "level_p90_dbfs": _r(segment_p90, 1),
                "baseline_dbfs": _r(baseline, 1),
                "relative_db": _r(relative_db, 1),
                "bot_overlap": bot_overlap,
                "echo_score": _r(echo_score),
                "guard_would_trigger": guard_would_trigger,
                "shadow_echo_drop": shadow_echo,
                "shadow_short_drop": shadow_short,
                "shadow_level": shadow_level,
            }
        )

    # -- call end ------------------------------------------------------------

    @property
    def input_audio_seconds(self) -> float:
        """Seconds of guest audio streamed into the pipeline (and so to
        Sarvam STT) -- the metered quantity for STT usage."""
        return self._input_audio_seconds

    def finalize(self) -> None:
        """Writes this call's single telemetry record into ConversationQuality.
        Idempotent; never raises (called from on_pipeline_finished)."""
        if self._finalized:
            return
        self._finalized = True
        try:
            now = self._monotonic()
            if self._pending_barge_in is not None:
                self._resolve_barge_in("no_transcript")
            noise_floor = _histogram_percentile(self._level_histogram, 10)
            speech_level = _histogram_percentile(self._level_histogram, 90)
            summary = {
                "telemetry_version": TELEMETRY_VERSION,
                # The PIPELINE's input rate, after the serializer resamples --
                # Exotel's 8 kHz audio arrives here as 16 kHz too, so this is
                # NOT a phone-vs-browser indicator (use the call's channel).
                "input_sample_rate": self._input_sample_rate,
                "input_audio_seconds": _r(self._input_audio_seconds, 1),
                "bot_speaking_seconds": _r(self._context.bot_speaking_seconds(now), 1),
                "noise_floor_dbfs": _r(noise_floor, 1),
                "median_dbfs": _r(_histogram_percentile(self._level_histogram, 50), 1),
                "speech_level_dbfs": _r(speech_level, 1),
                "est_snr_db": _r(speech_level - noise_floor, 1)
                if (speech_level is not None and noise_floor is not None)
                else None,
                "caller_baseline_dbfs": _r(_percentile(list(self._baseline_levels), 50), 1),
                "lang_prob_p10": _r(_percentile(self._language_probabilities, 10), 3),
                "lang_prob_p50": _r(_percentile(self._language_probabilities, 50), 3),
                "sarvam_vad_events": dict(self._context.sarvam_vad_events),
                "lang_counts": dict(self._lang_counts),
                "observer_overhead_ms": _r(self._processing_seconds * 1000.0, 1),
                **{key: value for key, value in sorted(self._counters.items())},
            }
            self._quality.record(
                ValidationResult(
                    rule=AUDIO_INPUT_TELEMETRY_RULE,
                    severity="INFO",
                    confidence=1.0,
                    turn_index=self._counters["segments_total"],
                    processing_time_ms=self._processing_seconds * 1000.0,
                    metadata={"summary": summary, "segments": self._segments, "barge_ins": self._barge_ins},
                )
            )
            logger.info(
                "audio_input_telemetry segments={} barge_ins={} phantom_barge_ins={} guard_triggers={} "
                "shadow_echo={} shadow_short={} shadow_level_drop={} shadow_level_clarify={} "
                "snr_db={} sarvam_vad_events={}",
                summary.get("segments_total", 0),
                summary.get("barge_ins_total", 0),
                summary.get("phantom_barge_ins", 0),
                summary.get("guard_triggers", 0),
                summary.get("shadow_echo_drop", 0),
                summary.get("shadow_short_drop", 0),
                summary.get("shadow_level_drop", 0),
                summary.get("shadow_level_clarify", 0),
                summary["est_snr_db"],
                summary["sarvam_vad_events"],
            )
        except Exception:
            logger.exception("audio_input_telemetry_finalize_failed")
