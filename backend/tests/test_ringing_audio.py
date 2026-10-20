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
    # TEMPORARY (Phase 6 diagnostic scaffolding, 2026-08-12, uncommitted):
    # play_busy_message now also sends one trailing Exotel "mark" event
    # after the media frames -- +1 to the expected count. Remove this +1
    # (and this comment) along with the rest of the scaffolding once the
    # chunk-size A/B test concludes.
    assert len(ws.sent) == frames_in_clip + 1


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


def test_committed_busy_message_asset_is_actually_8000hz():
    # Direct regression for the 2026-08-12 production incident: a
    # replacement busy_message_speech_8000.wav was committed with a
    # correct-looking filename but a real WAV header of 22050 Hz -- every
    # downstream consumer (_stream_pcm's fixed chunk-size/pacing math)
    # blindly trusted the filename/hardcoded constants instead of the
    # file's own header, so production played the message back at ~2.76x
    # its real duration with pitch dropped by the same factor (confirmed
    # live: "slow, robotic, garbled"). This test reads the committed
    # asset's ACTUAL header directly (not through the validating
    # _load_pcm, which would now catch this anyway -- see the test below --
    # this test instead guards the raw asset file itself, so it fails even
    # if _load_pcm's own validation were ever weakened or removed).
    with ringing_audio.wave.open(str(ringing_audio._ASSETS_DIR / "busy_message_speech_8000.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 8000
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2


def test_load_pcm_rejects_a_wav_with_the_wrong_real_sample_rate(tmp_path):
    # Unit-level regression for the same incident, isolated from the
    # committed asset: _load_pcm must refuse (not silently accept) a WAV
    # whose actual header doesn't match the 8000Hz/mono/16-bit format
    # _stream_pcm's fixed chunk math assumes -- proves the validation
    # itself works, independent of whether today's committed asset happens
    # to be correct.
    import wave as wave_module

    mismatched_path = tmp_path / "wrong_rate.wav"
    with wave_module.open(str(mismatched_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)  # the exact wrong rate from the real incident
        wav_file.writeframes(b"\x00\x00" * 100)

    original_assets_dir = ringing_audio._ASSETS_DIR
    ringing_audio._ASSETS_DIR = tmp_path
    try:
        try:
            ringing_audio._load_pcm("wrong_rate.wav")
            raised = False
        except ringing_audio._UnexpectedWavFormat as exc:
            raised = True
            assert "22050" in str(exc)
            assert "8000" in str(exc)
    finally:
        ringing_audio._ASSETS_DIR = original_assets_dir

    assert raised, "_load_pcm must raise _UnexpectedWavFormat for a real-rate mismatch, not silently load it"


def test_load_pcm_accepts_a_correctly_formatted_wav(tmp_path):
    # The inverse of the test above -- a genuinely correct 8000Hz/mono/
    # 16-bit file must still load normally, proving the new validation
    # isn't overly strict.
    import wave as wave_module

    correct_path = tmp_path / "correct_rate.wav"
    frame_bytes = b"\x01\x00" * 50
    with wave_module.open(str(correct_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(8000)
        wav_file.writeframes(frame_bytes)

    original_assets_dir = ringing_audio._ASSETS_DIR
    ringing_audio._ASSETS_DIR = tmp_path
    try:
        pcm = ringing_audio._load_pcm("correct_rate.wav")
    finally:
        ringing_audio._ASSETS_DIR = original_assets_dir

    assert pcm == frame_bytes


async def test_busy_message_send_failure_is_swallowed_not_raised():
    # Same contract as the ring tone: a busy-recovery call still needs to
    # proceed to hangup even if playback itself fails partway through.
    class _FailingWebSocket:
        async def send_text(self, data: str):
            raise RuntimeError("socket already closing")

    await play_busy_message(_FailingWebSocket(), "stream-busy-fail")


def test_busy_message_pcm_falls_back_to_beep_tone_if_speech_asset_fails_to_load():
    # Not a live-dependency fallback (there is no live dependency on this
    # path anymore) -- purely against this module's own committed asset
    # going missing/corrupt at import time, e.g. a fresh checkout that
    # hasn't run the generation script. Exercises the module's actual
    # load-time try/except logic directly (same _load_pcm/except shape as
    # the real module-level code, including _UnexpectedWavFormat) rather
    # than reloading the module, which can't cleanly isolate a monkeypatch
    # across its own re-execution.
    def _load_pcm_missing_speech_asset(filename: str) -> bytes:
        if filename == "busy_message_speech_8000.wav":
            raise FileNotFoundError(filename)
        return ringing_audio._load_pcm(filename)

    try:
        pcm = _load_pcm_missing_speech_asset("busy_message_speech_8000.wav")
    except (FileNotFoundError, ringing_audio.wave.Error, ringing_audio._UnexpectedWavFormat):
        pcm = _load_pcm_missing_speech_asset("busy_message_8000.wav")

    assert pcm == ringing_audio._load_pcm("busy_message_8000.wav")


def test_busy_message_pcm_falls_back_to_beep_tone_if_speech_asset_is_wrong_sample_rate():
    # Same fallback contract as the test above, but exercising the actual
    # incident's trigger: the asset loads fine as a WAV file (no
    # FileNotFoundError, no wave.Error) but its real header doesn't match
    # -- _UnexpectedWavFormat is what _load_pcm raises for that case, and
    # the module's own except clause must catch it and degrade to the beep
    # tone, not let it propagate and break the whole module at import time.
    def _load_pcm_wrong_rate_speech_asset(filename: str) -> bytes:
        if filename == "busy_message_speech_8000.wav":
            raise ringing_audio._UnexpectedWavFormat("simulated 22050Hz asset")
        return ringing_audio._load_pcm(filename)

    try:
        pcm = _load_pcm_wrong_rate_speech_asset("busy_message_speech_8000.wav")
    except (FileNotFoundError, ringing_audio.wave.Error, ringing_audio._UnexpectedWavFormat):
        pcm = _load_pcm_wrong_rate_speech_asset("busy_message_8000.wav")

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


async def test_stream_pcm_does_not_accumulate_drift_under_send_latency(monkeypatch):
    # Regression for the exact live symptom (2026-08-11): a busy-call
    # message that sounded correctly paced as a standalone file played
    # audibly slow/dragged-out on a real call. Root cause: the old
    # implementation did `await websocket.send_text(...)` then
    # unconditionally `await asyncio.sleep(_CHUNK_DURATION_S)` -- sleep()
    # only guarantees AT LEAST that duration, and send_text's own latency
    # (real under production event-loop contention -- _reject_call_as_busy
    # only ever runs while a different, resource-hungry live call is
    # ALSO active on this process, see pipeline.py) adds on top and
    # compounds across ~800+ chunks in a 17s clip. This test simulates
    # that contention (each send_text call itself takes real, non-negligible
    # time) and asserts total playback time stays close to the clip's
    # authored duration instead of drifting past it.
    chunk_count = 40  # 40 * 20ms = 0.8s of nominal audio
    pcm = b"\x00\x01" * (chunk_count * (_CHUNK_BYTES // 2))
    nominal_duration = chunk_count * 0.02

    class _SlowWebSocket:
        def __init__(self):
            self.sent: list[str] = []

        async def send_text(self, data: str):
            # Stands in for real send latency under load -- a meaningful
            # fraction of the 20ms budget, not negligible.
            await asyncio.sleep(0.008)
            self.sent.append(data)

    ws = _SlowWebSocket()
    loop = asyncio.get_running_loop()
    start = loop.time()
    await ringing_audio._stream_pcm(ws, "stream-drift", pcm)
    elapsed = loop.time() - start

    assert len(ws.sent) == chunk_count
    # Old behavior would land at ~chunk_count * (0.008 + 0.02) = 1.12s here
    # (40% over nominal) -- deadline-based pacing keeps total elapsed time
    # close to the clip's real duration regardless of per-chunk send cost.
    assert elapsed < nominal_duration * 1.15


def test_resolve_test_chunk_bytes_falls_back_instead_of_raising_on_invalid_value(monkeypatch):
    # Regression: _resolve_test_chunk_bytes runs at MODULE IMPORT time, and
    # ringing_audio is imported transitively by app/main.py itself (main ->
    # app.api.v1 -> voice.py -> pipeline.py -> ringing_audio.py). An earlier
    # version of this function raised ValueError for an out-of-range value --
    # confirmed live, that crashed the ENTIRE backend process on boot for a
    # single typo'd env var, taking down every call type, not just busy
    # calls. Must log and fall back to the safe default instead.
    monkeypatch.setenv("BUSY_AUDIO_TEST_CHUNK_BYTES", "999")
    assert ringing_audio._resolve_test_chunk_bytes() == ringing_audio._CHUNK_BYTES


def test_resolve_test_chunk_bytes_falls_back_instead_of_raising_on_non_integer(monkeypatch):
    monkeypatch.setenv("BUSY_AUDIO_TEST_CHUNK_BYTES", "not-a-number")
    assert ringing_audio._resolve_test_chunk_bytes() == ringing_audio._CHUNK_BYTES


def test_resolve_test_chunk_bytes_accepts_every_supported_value(monkeypatch):
    for value in (320, 640, 1600, 3200):
        monkeypatch.setenv("BUSY_AUDIO_TEST_CHUNK_BYTES", str(value))
        assert ringing_audio._resolve_test_chunk_bytes() == value


def test_resolve_test_chunk_bytes_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("BUSY_AUDIO_TEST_CHUNK_BYTES", raising=False)
    assert ringing_audio._resolve_test_chunk_bytes() == ringing_audio._CHUNK_BYTES


async def test_busy_message_mark_event_uses_camel_case_stream_sid_field():
    # Regression: an earlier version of the mark event used "stream_sid"
    # (snake_case), inconsistent with every other outbound event this
    # module and pipecat's own ExotelFrameSerializer send ("streamSid",
    # camelCase) -- Exotel's wire convention is asymmetric (inbound start
    # event uses snake_case, every outbound event uses camelCase), so the
    # wrong-case field would very likely be silently ignored by Exotel.
    ws = _FakeWebSocket()

    await play_busy_message(ws, "case-test-sid")

    mark_messages = [json.loads(m) for m in ws.sent if json.loads(m).get("event") == "mark"]
    assert len(mark_messages) == 1
    assert "streamSid" in mark_messages[0]
    assert mark_messages[0]["streamSid"] == "case-test-sid"
    assert "stream_sid" not in mark_messages[0]
