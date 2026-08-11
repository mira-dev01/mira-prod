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


def _load_pcm(filename: str) -> bytes:
    with wave.open(str(_ASSETS_DIR / filename), "rb") as wav_file:
        return wav_file.readframes(wav_file.getnframes())


# Read once at import time -- these are small, fixed, committed assets, not
# something that changes per call. See scripts/generate_ringing_tone.py and
# scripts/generate_busy_message_speech.py.
_RINGING_TONE_PCM = _load_pcm("ringing_tone_8000.wav")

try:
    # The real spoken busy-recovery message -- see scripts/
    # generate_busy_message_speech.py and the module docstring above.
    _BUSY_MESSAGE_PCM = _load_pcm("busy_message_speech_8000.wav")
except (FileNotFoundError, wave.Error):
    # Last-resort fallback purely against this module's own committed asset
    # going missing/corrupt -- e.g. a fresh checkout that hasn't run the
    # generation script yet. Not a live-dependency fallback (there is no
    # live dependency on this path anymore); a caller must still hear
    # *something* and get hung up on promptly rather than a silent socket.
    logger.error(
        "busy_message_speech_8000.wav missing/unreadable; falling back to "
        "the placeholder beep tone. Run scripts/generate_busy_message_speech.py."
    )
    _BUSY_MESSAGE_PCM = _load_pcm("busy_message_8000.wav")


async def _stream_pcm(websocket: WebSocket, stream_sid: str, pcm: bytes) -> None:
    """Writes one pass of `pcm` to `websocket` as real-time-paced Exotel
    media-event chunks. The one place both playback modes below encode
    Exotel's wire format and pace chunks -- see module docstring."""
    for offset in range(0, len(pcm), _CHUNK_BYTES):
        chunk = pcm[offset : offset + _CHUNK_BYTES]
        payload = base64.b64encode(chunk).decode("ascii")
        message = {"event": "media", "streamSid": stream_sid, "media": {"payload": payload}}
        await websocket.send_text(json.dumps(message))
        await asyncio.sleep(_CHUNK_DURATION_S)


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
    """
    logger.info("busy_recovery_audio_started stream_sid=%s", stream_sid)
    try:
        await _stream_pcm(websocket, stream_sid, _BUSY_MESSAGE_PCM)
        logger.info("busy_recovery_audio_completed stream_sid=%s", stream_sid)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Busy message playback failed; call proceeds to hangup regardless.", exc_info=True)
