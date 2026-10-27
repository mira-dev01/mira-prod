"""Covers Phase 1A audio-input telemetry (app/voice/audio_input_observer.py):
observation only -- every frame must pass through unchanged -- plus the
per-segment analysis, shadow verdicts, and barge-in attribution that end up
in the single per-call telemetry record.

Logic tests drive the observer's synchronous _observe() with a fake clock
instead of a real pipeline, so timing (bot-speaking windows, phantom
barge-in expiry) is deterministic.
"""

import numpy as np
import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    InterruptionFrame,
    TranscriptionFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.tests.utils import run_test

from app.models.call_quality_event import AUDIO_INPUT_TELEMETRY_RULE
from app.voice.audio_input_observer import (
    AudioInputContext,
    AudioInputObserver,
    BotSpeechTap,
    _frame_dbfs,
    _normalize,
)
from app.voice.conversation_quality import ConversationQuality

DOWN = FrameDirection.DOWNSTREAM
UP = FrameDirection.UPSTREAM


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


def _make(clock: FakeClock):
    context = AudioInputContext(clock=clock)
    quality = ConversationQuality()
    return context, quality, AudioInputObserver(context, quality)


def _audio(amplitude: int, ms: int = 20, sample_rate: int = 8000) -> InputAudioRawFrame:
    samples = np.full(int(sample_rate * ms / 1000), amplitude, dtype=np.int16)
    return InputAudioRawFrame(audio=samples.tobytes(), sample_rate=sample_rate, num_channels=1)


def _transcript(text: str, prob: float | None = 0.9, audio_duration: float | None = None) -> TranscriptionFrame:
    data = {"transcript": text, "language_code": "hi-IN"}
    if prob is not None:
        data["language_probability"] = prob
    if audio_duration is not None:
        data["metrics"] = {"audio_duration": audio_duration, "processing_latency": 0.2}
    return TranscriptionFrame(text=text, user_id="guest", timestamp="", result={"type": "data", "data": data})


def _telemetry(observer: AudioInputObserver, quality: ConversationQuality) -> dict:
    observer.finalize()
    records = [v for v in quality.validations if v.rule == AUDIO_INPUT_TELEMETRY_RULE]
    assert len(records) == 1
    return records[0].metadata


@pytest.mark.asyncio
async def test_every_frame_passes_through_unchanged():
    context = AudioInputContext()
    observer = AudioInputObserver(context, ConversationQuality())
    frames = [_audio(1000), _transcript("Okay", prob=0.055), _audio(500)]

    down, _ = await run_test(observer, frames_to_send=frames)

    transcripts = [f for f in down if isinstance(f, TranscriptionFrame)]
    audio = [f for f in down if isinstance(f, InputAudioRawFrame)]
    assert [t.text for t in transcripts] == ["Okay"]
    assert len(audio) == 2


@pytest.mark.asyncio
async def test_bot_speech_tap_records_spoken_text_and_passes_through():
    context = AudioInputContext()
    tap = BotSpeechTap(context)

    down, _ = await run_test(tap, frames_to_send=[TTSTextFrame("Aapke kitne guests hain?", aggregated_by="sentence")])

    assert any(isinstance(f, TTSTextFrame) for f in down)
    assert "kitne guests" in context.recent_bot_text(since=0)


def test_low_probability_short_transcript_is_recorded_as_guard_trigger_with_text():
    clock = FakeClock()
    _, quality, observer = _make(clock)

    observer._observe(_transcript("Okay", prob=0.055), DOWN)
    meta = _telemetry(observer, quality)

    segment = meta["segments"][0]
    assert segment["guard_would_trigger"] is True
    assert segment["short_text"] == "Okay"
    assert segment["token_class"] == "backchannel"
    assert meta["summary"]["guard_triggers"] == 1
    assert meta["summary"]["guard_triggers_short"] == 1


def test_long_transcript_text_is_not_stored():
    clock = FakeClock()
    _, quality, observer = _make(clock)

    observer._observe(_transcript("mujhe Goa mein do raat ke liye villa chahiye", prob=0.7), DOWN)
    segment = _telemetry(observer, quality)["segments"][0]

    assert segment["short_text"] is None
    assert segment["words"] == 9
    assert segment["guard_would_trigger"] is False


def test_echo_of_bot_speech_is_shadow_flagged():
    clock = FakeClock()
    context, quality, observer = _make(clock)

    observer._observe(BotStartedSpeakingFrame(), UP)
    context.record_bot_text("Aapke kitne guests hain")
    clock.advance(1.0)
    observer._observe(_transcript("kitne guests hain", audio_duration=1.0), DOWN)
    meta = _telemetry(observer, quality)

    segment = meta["segments"][0]
    assert segment["bot_overlap"] is True
    assert segment["echo_score"] == 1.0
    assert segment["shadow_echo_drop"] is True
    assert meta["summary"]["shadow_echo_drop"] == 1


def test_short_segment_during_bot_speech_is_shadow_flagged_but_stop_words_are_not():
    clock = FakeClock()
    context, quality, observer = _make(clock)

    observer._observe(BotStartedSpeakingFrame(), UP)
    clock.advance(0.5)
    observer._observe(_transcript("haan", audio_duration=0.3), DOWN)
    clock.advance(0.5)
    observer._observe(_transcript("ruko", audio_duration=0.3), DOWN)
    clock.advance(0.5)
    observer._observe(_transcript("29", audio_duration=0.3), DOWN)
    meta = _telemetry(observer, quality)

    by_text = {s["short_text"]: s for s in meta["segments"]}
    assert by_text["haan"]["shadow_short_drop"] is True
    assert by_text["ruko"]["shadow_short_drop"] is False
    assert by_text["ruko"]["token_class"] == "stop_word"
    assert by_text["29"]["token_class"] == "number"
    assert meta["summary"]["shadow_short_drop_number"] == 1


def test_short_segment_while_bot_silent_is_not_flagged():
    clock = FakeClock()
    _, quality, observer = _make(clock)

    observer._observe(_transcript("haan", audio_duration=0.3), DOWN)
    segment = _telemetry(observer, quality)["segments"][0]

    assert segment["bot_overlap"] is False
    assert segment["shadow_short_drop"] is False


def test_quiet_segment_relative_to_caller_baseline_is_shadow_flagged():
    clock = FakeClock()
    _, quality, observer = _make(clock)

    # Two clean, loud caller turns establish a baseline (~ -10 dBFS)...
    for _ in range(2):
        for _ in range(50):
            observer._observe(_audio(10000), DOWN)
            clock.advance(0.02)
        observer._observe(_transcript("teen log aayenge december mein", audio_duration=1.0), DOWN)
        clock.advance(2.0)
    # ...then a much quieter one (~ -40 dBFS): a background talker.
    for _ in range(50):
        observer._observe(_audio(330), DOWN)
        clock.advance(0.02)
    observer._observe(_transcript("kya bol rahe ho", audio_duration=1.0), DOWN)
    meta = _telemetry(observer, quality)

    quiet = meta["segments"][-1]
    assert quiet["baseline_dbfs"] is not None
    assert quiet["relative_db"] <= -12
    assert quiet["shadow_level"] == "clarify"
    assert meta["summary"]["shadow_level_clarify"] == 1


def test_barge_in_source_is_attributed_by_direction_and_phantom_detected():
    clock = FakeClock()
    _, quality, observer = _make(clock)

    observer._observe(BotStartedSpeakingFrame(), UP)
    clock.advance(1.0)
    # Downstream interruption at the observer == broadcast by stt (Sarvam VAD).
    observer._observe(InterruptionFrame(), DOWN)
    observer._observe(BotStoppedSpeakingFrame(), UP)
    clock.advance(0.5)
    # Mira resumes without the guest ever having said anything: phantom.
    observer._observe(BotStartedSpeakingFrame(), UP)
    clock.advance(2.0)
    # Upstream interruption == the user aggregator's turn controller.
    observer._observe(InterruptionFrame(), UP)
    observer._observe(BotStoppedSpeakingFrame(), UP)
    clock.advance(1.5)
    observer._observe(_transcript("mujhe price batao please", audio_duration=1.0), DOWN)
    meta = _telemetry(observer, quality)

    summary = meta["summary"]
    assert summary["barge_ins_total"] == 2
    assert summary["barge_ins_sarvam_stt"] == 1
    assert summary["barge_ins_downstream_turn_controller"] == 1
    assert summary["phantom_barge_ins"] == 1
    outcomes = [b["outcome"] for b in meta["barge_ins"]]
    assert outcomes == ["no_transcript_bot_resumed", "transcript"]


def test_same_speech_event_from_both_sources_is_one_barge_in_not_a_phantom():
    """With Sarvam's VAD events active, one guest speech-start produces an
    interruption from stt AND one from the aggregator. That must count as a
    single barge-in -- never a second one, and never a false phantom."""
    clock = FakeClock()
    _, quality, observer = _make(clock)

    observer._observe(BotStartedSpeakingFrame(), UP)
    clock.advance(1.0)
    observer._observe(InterruptionFrame(), DOWN)  # stt / Sarvam START_SPEECH
    clock.advance(0.3)
    observer._observe(InterruptionFrame(), UP)  # aggregator turn start
    observer._observe(BotStoppedSpeakingFrame(), UP)
    clock.advance(1.5)
    observer._observe(_transcript("price kya hai", audio_duration=1.0), DOWN)
    meta = _telemetry(observer, quality)

    summary = meta["summary"]
    assert summary["barge_ins_total"] == 1
    assert summary["barge_ins_dual_source"] == 1
    assert "phantom_barge_ins" not in summary
    assert meta["barge_ins"][0]["source"] == "sarvam_stt"
    assert meta["barge_ins"][0]["also_from"] == "downstream_turn_controller"
    assert meta["barge_ins"][0]["outcome"] == "transcript"


def test_transcript_triggered_barge_in_resolves_against_the_preceding_transcript():
    """pipecat's TranscriptionUserTurnStartStrategy starts the turn (and so
    broadcasts the interruption) from a transcript that arrived while Mira
    spoke -- the interruption lands AFTER its own transcript and must not be
    left pending until it expires as a false phantom."""
    clock = FakeClock()
    _, quality, observer = _make(clock)

    observer._observe(BotStartedSpeakingFrame(), UP)
    clock.advance(2.0)
    observer._observe(_transcript("ek minute ruko please", audio_duration=1.0), DOWN)
    clock.advance(0.1)
    observer._observe(InterruptionFrame(), UP)
    observer._observe(BotStoppedSpeakingFrame(), UP)
    clock.advance(10.0)
    observer._observe(_audio(100), DOWN)
    meta = _telemetry(observer, quality)

    barge_in = meta["barge_ins"][0]
    assert barge_in["trigger"] == "transcript"
    assert barge_in["outcome"] == "transcript"
    assert meta["summary"]["barge_ins_transcript_triggered"] == 1
    assert "phantom_barge_ins" not in meta["summary"]


def test_transcript_triggered_barge_in_from_echo_is_marked_suspect():
    clock = FakeClock()
    context, quality, observer = _make(clock)

    observer._observe(BotStartedSpeakingFrame(), UP)
    context.record_bot_text("Aapke kitne guests hain")
    clock.advance(1.0)
    observer._observe(_transcript("kitne guests hain", audio_duration=1.0), DOWN)
    clock.advance(0.1)
    observer._observe(InterruptionFrame(), UP)
    meta = _telemetry(observer, quality)

    assert meta["barge_ins"][0]["outcome"] == "transcript_suspect"
    assert meta["summary"]["phantom_barge_ins"] == 1


def test_language_mix_counts_multiword_segments_and_switches():
    clock = FakeClock()
    _, quality, observer = _make(clock)

    def lang_transcript(text, lang):
        frame = _transcript(text)
        frame.result["data"]["language_code"] = lang
        return frame

    observer._observe(lang_transcript("mujhe villa chahiye", "hi-IN"), DOWN)
    observer._observe(lang_transcript("for two nights please", "en-IN"), DOWN)
    observer._observe(lang_transcript("okay", "en-IN"), DOWN)  # 1 word: ignored
    observer._observe(lang_transcript("budget kitna hai", "hi-IN"), DOWN)
    summary = _telemetry(observer, quality)["summary"]

    assert summary["lang_counts"] == {"hi-IN": 2, "en-IN": 1}
    assert summary["lang_switches"] == 2


def test_interruption_while_bot_silent_is_counted_not_recorded_as_barge_in():
    clock = FakeClock()
    _, quality, observer = _make(clock)

    observer._observe(InterruptionFrame(), UP)
    meta = _telemetry(observer, quality)

    assert meta["barge_ins"] == []
    assert meta["summary"]["interruptions_bot_silent_downstream_turn_controller"] == 1
    assert "barge_ins_total" not in meta["summary"]


def test_pending_barge_in_expires_as_phantom_after_window():
    clock = FakeClock()
    _, quality, observer = _make(clock)

    observer._observe(BotStartedSpeakingFrame(), UP)
    observer._observe(InterruptionFrame(), UP)
    clock.advance(9.0)
    observer._observe(_audio(100), DOWN)
    meta = _telemetry(observer, quality)

    assert meta["barge_ins"][0]["outcome"] == "no_transcript"
    assert meta["summary"]["phantom_barge_ins"] == 1


def test_sarvam_vad_events_are_counted_into_summary():
    clock = FakeClock()
    context, quality, observer = _make(clock)

    context.note_sarvam_vad_event("START_SPEECH")
    context.note_sarvam_vad_event("END_SPEECH")
    context.note_sarvam_vad_event("START_SPEECH")
    meta = _telemetry(observer, quality)

    assert meta["summary"]["sarvam_vad_events"] == {"START_SPEECH": 2, "END_SPEECH": 1}


def test_summary_levels_and_input_duration():
    clock = FakeClock()
    _, quality, observer = _make(clock)

    for _ in range(50):
        observer._observe(_audio(100), DOWN)  # quiet floor
    for _ in range(50):
        observer._observe(_audio(10000), DOWN)  # speech
    summary = _telemetry(observer, quality)["summary"]

    assert summary["input_sample_rate"] == 8000
    assert summary["input_audio_seconds"] == 2.0
    assert summary["est_snr_db"] > 30
    assert summary["noise_floor_dbfs"] < summary["speech_level_dbfs"]


def test_finalize_is_idempotent():
    clock = FakeClock()
    _, quality, observer = _make(clock)

    observer.finalize()
    observer.finalize()

    assert len([v for v in quality.validations if v.rule == AUDIO_INPUT_TELEMETRY_RULE]) == 1


def test_segment_records_are_capped_but_counters_stay_exact(monkeypatch):
    import app.voice.audio_input_observer as module

    monkeypatch.setattr(module, "MAX_SEGMENT_RECORDS", 3)
    clock = FakeClock()
    _, quality, observer = _make(clock)

    for _ in range(5):
        observer._observe(_transcript("ek do teen"), DOWN)
        clock.advance(1.0)
    meta = _telemetry(observer, quality)

    assert len(meta["segments"]) == 3
    assert meta["summary"]["segments_total"] == 5
    assert meta["summary"]["records_truncated"] == 2


def test_observer_never_raises_on_malformed_frames():
    """A transcript with a non-dict result or odd metrics must not break the
    call -- fail open like every other optional signal."""
    clock = FakeClock()
    _, quality, observer = _make(clock)

    bad = TranscriptionFrame(text="hello", user_id="guest", timestamp="", result="not-a-dict")
    observer._observe(bad, DOWN)
    weird = TranscriptionFrame(
        text="hello ji",
        user_id="guest",
        timestamp="",
        result={"data": {"language_probability": "x", "metrics": {"audio_duration": "y"}}},
    )
    observer._observe(weird, DOWN)

    assert _telemetry(observer, quality)["summary"]["segments_total"] == 2


def test_normalize_keeps_devanagari_marks_and_drops_punctuation():
    assert _normalize("रुकिए, please!") == "रुकिए please"


def test_frame_dbfs_silence_and_full_scale():
    assert _frame_dbfs(b"") == -100.0
    assert _frame_dbfs(np.zeros(160, dtype=np.int16).tobytes()) == -100.0
    assert _frame_dbfs(np.full(160, 32767, dtype=np.int16).tobytes()) > -0.1
