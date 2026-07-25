"""Plays a looping phone-ringing tone directly on the raw Exotel WebSocket
while run_voice_pipeline's setup (DB lookups, pipeline build, Sarvam STT/TTS
connect) is still in progress -- confirmed via call logs that this setup
takes ~4-6s, which the guest otherwise hears as dead air before Mira's first
word. Replaces the earlier holding-message feature (a spoken "connecting you
now" line, since removed) with a plain ringback tone -- nothing to say
means nothing that can be caught mid-sentence, so the only correctness
requirement is that playback actually stops before the real pipeline's own
audio starts, not that it stops at a "natural" point.

This writes Exotel's wire protocol directly (see
pipecat.serializers.exotel.ExotelFrameSerializer.serialize for the same
{"event": "media", ...} shape) rather than going through pipecat, because
the pipeline/transport don't exist yet at this point in the call -- there's
nothing to push a frame into.

Airtight stop guarantee: the caller (app/voice/pipeline.py) cancels this
task's asyncio.Task and awaits it to completion immediately before the real
transport starts writing to the same socket (runner.run(), which triggers
transport.start() -> first real audio out). That await only returns once
this coroutine has actually unwound -- asyncio never runs two coroutines'
code concurrently on the same event loop thread, so there is no interleaving
window where both this loop and the real transport could write at once.
This is the same placement/guarantee the removed holding-message feature
used; looping indefinitely here (instead of playing a fixed clip once)
doesn't change that guarantee, since exit is still driven entirely by
CancelledError, never by the clip reaching its end.
"""

import asyncio
import base64
import json
import logging
import wave
from pathlib import Path

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_ASSET_PATH = Path(__file__).parent / "assets" / "ringing_tone_8000.wav"

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


def _load_pcm() -> bytes:
    with wave.open(str(_ASSET_PATH), "rb") as wav_file:
        return wav_file.readframes(wav_file.getnframes())


# Read once at import time -- this is a small (~64KB), fixed, committed
# asset (one full ring cycle: 1s tone + 3s silence), not something that
# changes per call. See scripts/generate_ringing_tone.py.
_RINGING_TONE_PCM = _load_pcm()


async def play_ringing_tone(websocket: WebSocket, stream_sid: str) -> None:
    """Loops the ring-cycle clip to `websocket` until cancelled. Callers
    should asyncio.create_task() this and cancel + await the task once the
    real pipeline's own greeting is about to play (see
    app/voice/pipeline.py's cancellation point right before runner.run()) --
    cancellation is the ONLY way this coroutine ever exits; it never
    completes on its own. That's deliberate: unlike the old fixed-length
    holding message, a ring tone has no natural end to wait for -- the
    guarantee that it stops before Mira speaks comes entirely from the
    caller cancelling this task and awaiting it before the real transport
    takes over the socket, not from anything in this function noticing
    setup is done.
    """
    try:
        while True:
            for offset in range(0, len(_RINGING_TONE_PCM), _CHUNK_BYTES):
                chunk = _RINGING_TONE_PCM[offset : offset + _CHUNK_BYTES]
                payload = base64.b64encode(chunk).decode("ascii")
                message = {"event": "media", "streamSid": stream_sid, "media": {"payload": payload}}
                await websocket.send_text(json.dumps(message))
                await asyncio.sleep(_CHUNK_DURATION_S)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Best-effort: a ringing tone that fails to play is never worth
        # taking the call down over -- the real pipeline is what matters.
        logger.warning("Ringing tone playback failed; call continues normally.", exc_info=True)
