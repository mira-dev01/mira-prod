"""Plays audio directly on the raw Exotel WebSocket during the window
before the real pipecat pipeline/transport exists for a call -- either
looped (the ringback tone, while run_voice_pipeline's own setup: DB
lookups, pipeline build, Sarvam STT/TTS connect -- is in progress, confirmed
via call logs to take ~4-6s, which the guest otherwise hears as dead air
before Mira's first word) or played once (a short spoken message when
CallCoordinator rejects the call as BUSY_RECOVERY -- see pipeline.py -- and
no real pipeline will be built for this call at all).

Both write Exotel's wire protocol directly (see
pipecat.serializers.exotel.ExotelFrameSerializer.serialize for the same
{"event": "media", ...} shape) rather than going through pipecat, because
in both cases the pipeline/transport don't exist yet -- there's nothing to
push a frame into. `_stream_pcm` below is the one chunking/pacing primitive
both playback modes share, so there is exactly one place that encodes
Exotel's wire format and one place that paces chunks at real-time speed --
see the module docstring history: an earlier "holding message" feature
(app/voice/holding_audio.py, removed 2026-07-22) already independently
duplicated this exact chunking logic once, and its removal writeup names
that split as part of what made the whole feature fragile to maintain.

Airtight stop guarantee for the looped ringback: the caller
(app/voice/pipeline.py) cancels play_ringing_tone's asyncio.Task and awaits
it to completion immediately before the real transport starts writing to
the same socket (runner.run(), which triggers transport.start() -> first
real audio out). That await only returns once the coroutine has actually
unwound -- asyncio never runs two coroutines' code concurrently on the same
event loop thread, so there is no interleaving window where both this loop
and the real transport could write at once. play_busy_message (BUSY_RECOVERY)
never races a real transport at all -- no pipeline is ever built on that
path, so there's nothing else writing to the socket to conflict with.

BUSY_MESSAGE_TEXT is a fixed, static string -- no property, pricing,
availability, or guest-specific data is ever interpolated into it (single
call site, pipeline.py's _reject_call_as_busy, always passes the same fixed
text). Because of that, it's synthesized exactly ONCE, offline, by
scripts/generate_busy_message_speech.py, and committed as a static WAV
asset (busy_message_speech_8000.wav) -- the same approach
generate_ringing_tone.py already uses for the ring tone. play_busy_message
below makes zero network calls: no live Sarvam TTS request, no LLM, no STT,
just streaming a pre-generated file, same as play_ringing_tone. An earlier
version of this fix called Sarvam TTS live on every busy call; that was
unnecessary latency (~1-2s) and per-call cost for text that never changes,
and (confirmed live 2026-08-10) an extra live dependency that can itself
fail (e.g. account-level quota exhaustion) for no benefit, since the text
is always identical anyway. busy_message_8000.wav (the original 3-beep
placeholder tone, see scripts/generate_busy_message_tone.py) is kept as a
last-resort fallback purely against this module's own asset failing to
load at import time -- not against any live dependency, since there no
longer is one on this path.
"""

import asyncio
import base64
import json
import logging
import os
import time
import wave
from pathlib import Path

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_ASSETS_DIR = Path(__file__).parent / "assets"

# Source of truth for what busy_message_speech_8000.wav actually says --
# read by scripts/generate_busy_message_speech.py to (re)generate that
# asset. Not used for any live synthesis call; see module docstring.
BUSY_MESSAGE_TEXT = (
    "Hi! I'm helping another guest right now. I've sent you the details on "
    # Lowercase "whatsapp" deliberately -- Sarvam TTS mis-articulates the
    # capitalized brand spelling "WhatsApp" (confirmed by ear across
    # several respellings tried live); the plain lowercase form is the one
    # that reads back correctly. Bake-once asset (see
    # scripts/generate_busy_message_speech.py), so this is a one-time
    # wording choice for TTS legibility, not a general style rule.
    "whatsapp. Feel free to call back in 5 mins. Thank you!"
)

# Exotel's media stream protocol carries raw PCM in these frame events --
# 20ms is the conventional telephony chunk size (matches the pace real
# audio would arrive/play at), at 8000 Hz mono 16-bit PCM: 8000 * 0.02 * 2
# bytes/sample = 320 bytes/chunk. Sending the whole clip as one payload
# instead would arrive at Exotel as a single oversized burst rather than a
# real-time stream.
_CHUNK_DURATION_S = 0.02
_SAMPLE_RATE = 8000
_SAMPLE_WIDTH = 2
_CHUNK_BYTES = int(_SAMPLE_RATE * _CHUNK_DURATION_S) * _SAMPLE_WIDTH

# ============================================================================
# TEMPORARY DIAGNOSTIC SCAFFOLDING -- Phase 6 live Exotel chunk-size A/B test
# (2026-08-12). NOT part of the permanent architecture. Uncommitted; must be
# removed once the test matrix is complete and a production decision is made.
# Affects ONLY play_busy_message's own chunk size -- play_ringing_tone (the
# normal-call setup path) is completely untouched, still always uses the
# fixed 320-byte/_CHUNK_BYTES constant above, regardless of this env var.
#
# BUSY_AUDIO_TEST_CHUNK_BYTES: optional override for the busy-message chunk
# size only. Absent/unset => existing 320-byte behavior, byte-for-byte
# unchanged from before this scaffolding existed. Only exact multiples of
# 320 that evenly divide Exotel's documented safe range are accepted (320,
# 640, 1600, 3200); anything else is rejected at import time (fails loudly,
# not a silent fallback to a wrong value) so a typo in the env var can't
# silently run the wrong test.
_BUSY_AUDIO_TEST_CHUNK_BYTES_ALLOWED = (320, 640, 1600, 3200)


def _resolve_test_chunk_bytes() -> int:
    """Never raises: this runs at MODULE IMPORT time, and this module is
    imported transitively by app/main.py itself (main -> app.api.v1 ->
    voice.py -> pipeline.py -> ringing_audio.py) -- an earlier version of
    this function raised ValueError for an invalid env var, which meant a
    single typo'd BUSY_AUDIO_TEST_CHUNK_BYTES value would crash the ENTIRE
    backend process on boot, taking down every call type (not just busy
    calls), directly violating this scaffolding's own "must not affect
    normal calls" constraint. Logs loudly and falls back to the safe
    existing default (_CHUNK_BYTES, 320) instead."""
    raw = os.environ.get("BUSY_AUDIO_TEST_CHUNK_BYTES")
    if raw is None:
        return _CHUNK_BYTES
    try:
        value = int(raw)
    except ValueError:
        logger.error(
            "BUSY_AUDIO_TEST_CHUNK_BYTES=%r is not an integer -- "
            "must be one of %s or unset. Falling back to the default (%d).",
            raw,
            _BUSY_AUDIO_TEST_CHUNK_BYTES_ALLOWED,
            _CHUNK_BYTES,
        )
        return _CHUNK_BYTES
    if value not in _BUSY_AUDIO_TEST_CHUNK_BYTES_ALLOWED:
        logger.error(
            "BUSY_AUDIO_TEST_CHUNK_BYTES=%d is not one of the supported test values %s -- "
            "falling back to the default (%d) rather than guessing.",
            value,
            _BUSY_AUDIO_TEST_CHUNK_BYTES_ALLOWED,
            _CHUNK_BYTES,
        )
        return _CHUNK_BYTES
    return value


# Resolved once at import time, same lifecycle as every other module-level
# constant here -- a mid-process env var change was never supported by this
# module for anything else either (_SAMPLE_RATE, _CHUNK_BYTES, etc. are all
# import-time constants too).
_BUSY_AUDIO_TEST_CHUNK_BYTES = _resolve_test_chunk_bytes()
# chunk_duration = chunk_bytes / (sample_rate * channels * bytes_per_sample)
# -- exactly the formula requested, derived (not hardcoded per chunk size)
# so the deadline-pacing math below stays correct for whichever value is
# selected: 320->20ms, 640->40ms, 1600->100ms, 3200->200ms.
_BUSY_AUDIO_TEST_CHUNK_DURATION_S = _BUSY_AUDIO_TEST_CHUNK_BYTES / (_SAMPLE_RATE * 1 * _SAMPLE_WIDTH)
# ============================================================================


class _UnexpectedWavFormat(ValueError):
    """Raised by _load_pcm when a committed asset's actual WAV header
    doesn't match the format every downstream consumer (_stream_pcm's fixed
    _CHUNK_BYTES/_CHUNK_DURATION_S math, and Exotel's own wire protocol on
    this raw-websocket path) hardcodes and blindly trusts. Concrete
    incident this guards against (2026-08-12): a replacement
    busy_message_speech_8000.wav was committed with a correct-looking
    filename but a real header of 22050 Hz, not 8000 -- _load_pcm had no
    way to notice, so _stream_pcm silently chunked/paced 22050 Hz samples
    as if they were 8000 Hz, playing back at ~2.76x the correct duration
    with pitch dropped by the same factor (confirmed live: robotic,
    garbled, "slow" audio) instead of failing loudly. A ValueError subclass
    (not a bare assert/raise) so callers can catch it specifically -- see
    _BUSY_MESSAGE_PCM's fallback chain below, which must still degrade to
    the beep tone rather than crash the whole module on a bad asset."""


def _load_pcm(filename: str) -> bytes:
    with wave.open(str(_ASSETS_DIR / filename), "rb") as wav_file:
        actual_rate = wav_file.getframerate()
        actual_channels = wav_file.getnchannels()
        actual_width = wav_file.getsampwidth()
        if (actual_rate, actual_channels, actual_width) != (_SAMPLE_RATE, 1, _SAMPLE_WIDTH):
            raise _UnexpectedWavFormat(
                f"{filename}: expected {_SAMPLE_RATE}Hz/mono/{_SAMPLE_WIDTH * 8}-bit, "
                f"got {actual_rate}Hz/{actual_channels}ch/{actual_width * 8}-bit -- "
                "refusing to load, since _stream_pcm's fixed chunk-size/pacing math "
                "would silently mis-play it (see _UnexpectedWavFormat's own docstring)."
            )
        return wav_file.readframes(wav_file.getnframes())


# Read once at import time -- these are small, fixed, committed assets, not
# something that changes per call. See scripts/generate_ringing_tone.py and
# scripts/generate_busy_message_speech.py.
_RINGING_TONE_PCM = _load_pcm("ringing_tone_8000.wav")

try:
    # The real spoken busy-recovery message -- see scripts/
    # generate_busy_message_speech.py and the module docstring above.
    _BUSY_MESSAGE_PCM = _load_pcm("busy_message_speech_8000.wav")
except (FileNotFoundError, wave.Error, _UnexpectedWavFormat) as exc:
    # Last-resort fallback purely against this module's own committed asset
    # going missing/corrupt/wrong-format -- e.g. a fresh checkout that
    # hasn't run the generation script yet, or (the concrete incident
    # _UnexpectedWavFormat documents) a replacement file committed with the
    # right filename but the wrong real sample rate. Not a live-dependency
    # fallback (there is no live dependency on this path anymore); a caller
    # must still hear *something* correctly-paced and get hung up on
    # promptly, rather than either a crash or (the actual incident) audio
    # silently mis-played at the wrong speed/pitch.
    logger.error(
        "busy_message_speech_8000.wav missing/unreadable/wrong-format (%s); falling back to "
        "the placeholder beep tone. Run scripts/generate_busy_message_speech.py.",
        exc,
    )
    _BUSY_MESSAGE_PCM = _load_pcm("busy_message_8000.wav")


async def _stream_pcm(
    websocket: WebSocket,
    stream_sid: str,
    pcm: bytes,
    *,
    chunk_bytes: int = _CHUNK_BYTES,
    chunk_duration_s: float = _CHUNK_DURATION_S,
    on_chunk_sent=None,
) -> None:
    """Writes one pass of `pcm` to `websocket` as real-time-paced Exotel
    media-event chunks. The one place both playback modes below encode
    Exotel's wire format and pace chunks -- see module docstring.

    Paced against an absolute deadline (loop.time() + N * chunk duration),
    not `await asyncio.sleep(_CHUNK_DURATION_S)` after every send -- that
    naive version only guarantees "sleep AT LEAST 20ms," never exactly
    20ms, and each send_text call itself takes some nonzero time too. On a
    quiet event loop the difference is imperceptible; under real
    contention it isn't; each chunk was made to lag a little further
    behind, error compounds over the ~800+ chunks (17s clip / 20ms) in a
    real busy message, and the fixed-cadence recording ends up audibly
    dragged out and choppy in production despite being generated at the
    right pace, and the exact CPU/IO-active source of that contention
    always is present for the busy-call path (this coroutine only ever
    runs while Mira is ALREADY mid-call for a different guest -- see
    pipeline.py's _reject_call_as_busy -- i.e. exactly while a real,
    resource-hungry STT/TTS/LLM pipeline is also active on this same
    process/event loop). Computing each chunk's target send time up front
    from a fixed start point and sleeping only the remaining gap to that
    deadline (clamped to >=0, since a chunk running behind must never sleep
    a NEGATIVE amount) keeps drift from accumulating: a late chunk sleeps
    less to catch back up, instead of every later chunk inheriting the
    previous one's lateness on top of its own.

    chunk_bytes/chunk_duration_s/on_chunk_sent: TEMPORARY Phase 6 diagnostic
    parameters (2026-08-12), all defaulting to the pre-existing fixed
    behavior -- play_ringing_tone's call site below passes none of these,
    so it is byte-for-byte unaffected. Only play_busy_message threads
    through the (possibly env-var-overridden) test chunk size/duration and
    an optional per-chunk diagnostic callback. Remove this parameterization
    once the chunk-size A/B test is complete.
    """
    loop = asyncio.get_running_loop()
    start = loop.time()
    chunk_index = 0
    for offset in range(0, len(pcm), chunk_bytes):
        chunk = pcm[offset : offset + chunk_bytes]
        payload = base64.b64encode(chunk).decode("ascii")
        message = {"event": "media", "streamSid": stream_sid, "media": {"payload": payload}}
        scheduled_elapsed = chunk_index * chunk_duration_s
        send_start = loop.time()
        await websocket.send_text(json.dumps(message))
        send_end = loop.time()
        if on_chunk_sent is not None:
            on_chunk_sent(
                chunk_index=chunk_index,
                chunk_bytes=len(chunk),
                scheduled_elapsed_s=scheduled_elapsed,
                actual_elapsed_s=send_end - start,
                send_latency_s=send_end - send_start,
            )
        chunk_index += 1
        target_time = start + chunk_index * chunk_duration_s
        remaining = target_time - loop.time()
        if remaining > 0:
            await asyncio.sleep(remaining)


async def play_ringing_tone(websocket: WebSocket, stream_sid: str) -> None:
    """Loops the ring-cycle clip to `websocket` until cancelled. Callers
    should asyncio.create_task() this and cancel + await the task once the
    real pipeline's own greeting is about to play (see
    app/voice/pipeline.py's cancellation point right before runner.run()) --
    cancellation is the ONLY way this coroutine ever exits; it never
    completes on its own. That's deliberate: unlike a fixed-length message,
    a ring tone has no natural end to wait for -- the guarantee that it
    stops before Mira speaks comes entirely from the caller cancelling this
    task and awaiting it before the real transport takes over the socket,
    not from anything in this function noticing setup is done.
    """
    try:
        while True:
            await _stream_pcm(websocket, stream_sid, _RINGING_TONE_PCM)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Best-effort: a ringing tone that fails to play is never worth
        # taking the call down over -- the real pipeline is what matters.
        logger.warning("Ringing tone playback failed; call continues normally.", exc_info=True)


async def play_busy_message(websocket: WebSocket, stream_sid: str) -> None:
    """Plays the pre-generated spoken busy-recovery clip once and returns --
    used when CallCoordinator rejects a call as BUSY_RECOVERY (see
    pipeline.py), where no real pipeline/transport will ever exist for this
    call, so (unlike play_ringing_tone) there is no real transport for this
    playback to race against and nothing further to hand the socket off to.
    No network call, no TTS, no LLM -- see module docstring. Best-effort,
    same as play_ringing_tone: a failed/partial play is never worth raising
    into the caller, which still needs to hang up the call either way.

    TEMPORARY Phase 6 diagnostic (2026-08-12): chunk size/duration are read
    from BUSY_AUDIO_TEST_CHUNK_BYTES (see _resolve_test_chunk_bytes above)
    instead of the fixed module constants, purely to support the live
    Exotel chunk-size A/B test -- absent/unset, behavior is byte-for-byte
    identical to before this scaffolding existed. Diagnostic logs below are
    metadata only (byte counts, timing, hashes) -- never PCM, base64
    payloads, phone numbers, or transcripts.
    """
    total_pcm_bytes = len(_BUSY_MESSAGE_PCM)
    chunk_count = len(range(0, total_pcm_bytes, _BUSY_AUDIO_TEST_CHUNK_BYTES))
    expected_duration_s = chunk_count * _BUSY_AUDIO_TEST_CHUNK_DURATION_S

    logger.info(
        "busy_audio_started stream_sid=%s busy_audio_test_chunk_bytes=%d sample_rate=%d "
        "total_pcm_bytes=%d expected_duration_s=%.3f chunk_count=%d",
        stream_sid,
        _BUSY_AUDIO_TEST_CHUNK_BYTES,
        _SAMPLE_RATE,
        total_pcm_bytes,
        expected_duration_s,
        chunk_count,
    )
    logger.info("busy_recovery_audio_started stream_sid=%s", stream_sid)

    first_sent_wall_time = None
    last_sent_wall_time = None

    def _on_chunk_sent(chunk_index, chunk_bytes, scheduled_elapsed_s, actual_elapsed_s, send_latency_s):
        nonlocal first_sent_wall_time, last_sent_wall_time
        now = time.monotonic()
        if first_sent_wall_time is None:
            first_sent_wall_time = now
            logger.info("first_media_sent stream_sid=%s", stream_sid)
        last_sent_wall_time = now
        if chunk_index < 3 or chunk_index == chunk_count - 1:
            logger.debug(
                "busy_audio_chunk stream_sid=%s chunk_index=%d chunk_bytes=%d "
                "scheduled_elapsed_s=%.4f actual_elapsed_s=%.4f send_latency_s=%.4f",
                stream_sid,
                chunk_index,
                chunk_bytes,
                scheduled_elapsed_s,
                actual_elapsed_s,
                send_latency_s,
            )

    try:
        await _stream_pcm(
            websocket,
            stream_sid,
            _BUSY_MESSAGE_PCM,
            chunk_bytes=_BUSY_AUDIO_TEST_CHUNK_BYTES,
            chunk_duration_s=_BUSY_AUDIO_TEST_CHUNK_DURATION_S,
            on_chunk_sent=_on_chunk_sent,
        )
        logger.info("last_media_sent stream_sid=%s", stream_sid)
        if first_sent_wall_time is not None and last_sent_wall_time is not None:
            logger.info(
                "busy_audio_elapsed stream_sid=%s actual_elapsed_duration_s=%.3f",
                stream_sid,
                last_sent_wall_time - first_sent_wall_time,
            )

        # Optional Exotel "mark" event (Phase 6 Section 6) -- sent, never
        # awaited-for-receipt: this raw-websocket busy path has never read
        # inbound messages (no websocket.receive() call exists anywhere on
        # this path, confirmed before adding this), and adding a concurrent
        # receive-loop here would be exactly the kind of call-lifecycle
        # complication the investigation was told to avoid introducing.
        # Fire-and-forget, matches this whole module's existing "never let
        # playback machinery block/risk the hangup that follows" discipline.
        try:
            mark_name = f"busy-audio-test-{_BUSY_AUDIO_TEST_CHUNK_BYTES}"
            await websocket.send_text(
                # streamSid (camelCase), NOT stream_sid -- matches this
                # module's own "media" event above and pipecat's own
                # ExotelFrameSerializer's "media"/"clear" events exactly.
                # Exotel's wire convention is asymmetric: INBOUND events
                # (its own "start" event) use stream_sid (snake_case,
                # confirmed via pipecat's parse_telephony_websocket), but
                # every OUTBOUND event this codebase sends uses camelCase --
                # an earlier version of this line used stream_sid here,
                # which would very likely have been silently ignored by
                # Exotel as an unrecognized field.
                json.dumps({"event": "mark", "streamSid": stream_sid, "mark": {"name": mark_name}})
            )
            logger.info("busy_audio_mark_sent stream_sid=%s mark_name=%s", stream_sid, mark_name)
        except Exception:
            logger.warning("busy_audio_mark_sent failed (non-fatal, diagnostic only)", exc_info=True)

        logger.info("busy_recovery_audio_completed stream_sid=%s", stream_sid)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Busy message playback failed; call proceeds to hangup regardless.", exc_info=True)
