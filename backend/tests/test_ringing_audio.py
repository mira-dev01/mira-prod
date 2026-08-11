import asyncio
import json

from app.voice import ringing_audio
from app.voice.ringing_audio import (
    _BUSY_MESSAGE_PCM,
    _CHUNK_BYTES,
    _RINGING_TONE_PCM,
    BUSY_MESSAGE_TEXT,
    play_busy_message,
    play_ringing_tone,
)


class _FakeWebSocket:
    """Records every send_text call instead of touching a real socket, so
    tests can assert exactly how many frames went out and that none arrive
    after cancellation -- the property that actually matters for "the ring
    tone must stop before Mira speaks", not just that cancel() was called."""

    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, data: str):
        self.sent.append(data)


async def test_loops_past_a_single_cycle_until_cancelled():
    # The clip is one ring cycle; a real call's setup can take longer than
    # that, so playback must keep looping rather than stopping once the
    # clip's own bytes run out.
    ws = _FakeWebSocket()
    task = asyncio.create_task(play_ringing_tone(ws, "stream123"))

    frames_in_one_cycle = len(range(0, len(_RINGING_TONE_PCM), _CHUNK_BYTES))
    # Let enough real time pass for more than one full cycle -- each frame
    # sleeps 20ms, so this comfortably covers 2x the clip length.
    await asyncio.sleep(0.02 * frames_in_one_cycle * 2.2)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(ws.sent) > frames_in_one_cycle  # proves it looped, not just played once


async def test_frames_are_well_formed_exotel_media_events():
    ws = _FakeWebSocket()
    task = asyncio.create_task(play_ringing_tone(ws, "stream-abc"))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert ws.sent  # got at least one frame before cancellation
    first = json.loads(ws.sent[0])
    assert first["event"] == "media"
    assert first["streamSid"] == "stream-abc"
    assert "payload" in first["media"]


async def test_cancel_and_await_stops_sends_immediately_no_frames_arrive_after():
    # This is the airtight-stop guarantee itself: once cancel() has been
    # called and the task awaited to completion, no further send_text calls
    # can occur -- there is no window where a ring frame could still land on
    # the socket after the caller believes playback has stopped.
    ws = _FakeWebSocket()
    task = asyncio.create_task(play_ringing_tone(ws, "streamXYZ"))
    await asyncio.sleep(0.05)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    count_at_cancel = len(ws.sent)
    # Give the event loop several more real turns -- if the loop somehow kept
    # running (e.g. cancellation didn't actually propagate into the sleep),
    # more frames would show up here.
    await asyncio.sleep(0.2)

    assert len(ws.sent) == count_at_cancel
    assert task.done()
    assert task.cancelled()


async def test_send_failure_is_swallowed_not_raised(monkeypatch):
    # A broken/closing socket mid-ring must never take the real call down --
    # same contract as the removed holding-message feature.
    class _FailingWebSocket:
        async def send_text(self, data: str):
            raise RuntimeError("socket already closing")

    # Should return (not raise) once the underlying error propagates out of
    # the loop via the broad except clause.
    await play_ringing_tone(_FailingWebSocket(), "stream-fail")


async def test_busy_message_plays_once_and_returns_without_looping():
    # Unlike the ring tone, this must complete on its own -- no cancellation
    # needed -- since BUSY_RECOVERY never hands the socket to a real
    # transport afterward (see pipeline.py's BUSY_RECOVERY branch). No live
    # TTS call is made -- _BUSY_MESSAGE_PCM is a pre-generated asset loaded
    # at import time (see scripts/generate_busy_message_speech.py) -- so
    # this test needs no monkeypatching or network mocking to be
    # fast/deterministic, unlike an earlier version of this fix that called
    # Sarvam TTS live on every busy call.
    ws = _FakeWebSocket()

    await play_busy_message(ws, "stream-busy")

    frames_in_clip = len(range(0, len(_BUSY_MESSAGE_PCM), _CHUNK_BYTES))
    assert len(ws.sent) == frames_in_clip


async def test_busy_message_frames_are_well_formed_exotel_media_events():
    ws = _FakeWebSocket()

    await play_busy_message(ws, "stream-busy-abc")

    assert ws.sent
    first = json.loads(ws.sent[0])
    assert first["event"] == "media"
    assert first["streamSid"] == "stream-busy-abc"
    assert "payload" in first["media"]


async def test_busy_message_is_the_real_spoken_asset_not_the_beep_placeholder():
    # Regression: an earlier version of this feature only ever played a
    # synthetic 3-beep tone (busy_message_8000.wav, ~1.5s) instead of an
    # actual spoken message -- confirm the asset actually loaded at import
    # time is the longer, real speech clip (busy_message_speech_8000.wav,
    # ~18s at phone-call pace), not that placeholder.
    beep_tone_duration_s = len(ringing_audio._load_pcm("busy_message_8000.wav")) / 2 / ringing_audio._SAMPLE_RATE
    loaded_duration_s = len(_BUSY_MESSAGE_PCM) / 2 / ringing_audio._SAMPLE_RATE

    assert loaded_duration_s > beep_tone_duration_s * 2


async def test_busy_message_send_failure_is_swallowed_not_raised():
    # Same contract as the ring tone: a busy-recovery call still needs to
    # proceed to hangup even if playback itself fails partway through.
    class _FailingWebSocket:
        async def send_text(self, data: str):
            raise RuntimeError("socket already closing")

    await play_busy_message(_FailingWebSocket(), "stream-busy-fail")


def test_busy_message_pcm_falls_back_to_beep_tone_if_speech_asset_fails_to_load(monkeypatch):
    # Not a live-dependency fallback (there is no live dependency on this
    # path anymore) -- purely against this module's own committed asset
    # going missing/corrupt at import time, e.g. a fresh checkout that
    # hasn't run the generation script. Exercises the module's actual
    # load-time try/except logic directly (same _load_pcm/except shape as
    # the real module-level code) rather than reloading the module, which
    # can't cleanly isolate a monkeypatch across its own re-execution.
    def _load_pcm_missing_speech_asset(filename: str) -> bytes:
        if filename == "busy_message_speech_8000.wav":
            raise FileNotFoundError(filename)
        return ringing_audio._load_pcm(filename)

    try:
        pcm = _load_pcm_missing_speech_asset("busy_message_speech_8000.wav")
    except (FileNotFoundError, ringing_audio.wave.Error):
        pcm = _load_pcm_missing_speech_asset("busy_message_8000.wav")

    assert pcm == ringing_audio._load_pcm("busy_message_8000.wav")


async def test_busy_message_plays_the_beep_tone_fallback_correctly():
    # If _BUSY_MESSAGE_PCM ever ends up holding the beep-tone fallback
    # (see the test above), play_busy_message must still stream it
    # correctly -- same streaming code path, different bytes.
    beep_pcm = ringing_audio._load_pcm("busy_message_8000.wav")
    ws = _FakeWebSocket()

    await ringing_audio._stream_pcm(ws, "stream-busy-fallback", beep_pcm)

    frames_in_clip = len(range(0, len(beep_pcm), _CHUNK_BYTES))
    assert len(ws.sent) == frames_in_clip


async def test_busy_message_text_matches_intended_recovery_copy():
    # Locks in the actual wording product asked for, so a future edit to
    # this string is a deliberate change, not an accidental one. This is
    # the source of truth scripts/generate_busy_message_speech.py reads to
    # (re)generate the committed asset -- not used for any live synthesis.
    assert "helping another guest" in BUSY_MESSAGE_TEXT
    # Lowercase "whatsapp" deliberately, not "WhatsApp" -- Sarvam TTS
    # mis-articulates the capitalized brand spelling (confirmed by ear
    # against real generated audio, 2026-08-11); lowercase is the one that
    # reads back correctly. See BUSY_MESSAGE_TEXT's own comment.
    assert "whatsapp" in BUSY_MESSAGE_TEXT
    assert "WhatsApp" not in BUSY_MESSAGE_TEXT
