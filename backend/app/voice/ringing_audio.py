"""Plays fixed WAV clips directly on the raw Exotel WebSocket during the
window before the real pipecat pipeline/transport exists for a call --
either looped (the ringback tone, while run_voice_pipeline's own setup: DB
lookups, pipeline build, Sarvam STT/TTS connect -- is in progress, confirmed
via call logs to take ~4-6s, which the guest otherwise hears as dead air
before Mira's first word) or played once (a short placeholder message when
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
# scripts/generate_busy_message_tone.py.
_RINGING_TONE_PCM = _load_pcm("ringing_tone_8000.wav")
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
    """Plays the fixed busy-recovery placeholder clip once and returns --
    used when CallCoordinator rejects a call as BUSY_RECOVERY (see
    pipeline.py), where no real pipeline/transport will ever exist for this
    call, so (unlike play_ringing_tone) there is no real transport for this
    playback to race against and nothing further to hand the socket off to.
    Best-effort, same as play_ringing_tone: a failed/partial play is never
    worth raising into the caller, which still needs to hang up the call
    either way.
    """
    try:
        await _stream_pcm(websocket, stream_sid, _BUSY_MESSAGE_PCM)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Busy message playback failed; call proceeds to hangup regardless.", exc_info=True)
