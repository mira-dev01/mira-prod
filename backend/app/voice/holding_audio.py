"""Plays a short "connecting you now" message directly on the raw Exotel
WebSocket while run_voice_pipeline's setup (DB lookups, pipeline build,
Sarvam STT/TTS connect) is still in progress -- confirmed via call logs
that this setup takes ~4-5s, which the guest otherwise hears as dead air
before Mira's first word.

This writes Exotel's wire protocol directly (see
pipecat.serializers.exotel.ExotelFrameSerializer.serialize for the same
{"event": "media", ...} shape) rather than going through pipecat, because
the pipeline/transport don't exist yet at this point in the call -- there's
nothing to push a frame into.
"""

import asyncio
import base64
import json
import logging
import wave
from pathlib import Path

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_ASSET_PATH = Path(__file__).parent / "assets" / "holding_message_8000.wav"

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


# Read once at import time -- this is a small (~100KB), fixed, committed
# asset, not something that changes per call.
_HOLDING_MESSAGE_PCM = _load_pcm()


async def play_holding_message(websocket: WebSocket, stream_sid: str) -> None:
    """Streams the holding message to `websocket` until it finishes or is
    cancelled. Callers should asyncio.create_task() this and cancel the task
    once the real pipeline's own greeting is about to play (see
    app/voice/pipeline.py's _on_connected_greeting) -- cancellation is
    expected, not an error, since setup finishing before the clip ends is
    the whole point."""
    try:
        for offset in range(0, len(_HOLDING_MESSAGE_PCM), _CHUNK_BYTES):
            chunk = _HOLDING_MESSAGE_PCM[offset : offset + _CHUNK_BYTES]
            payload = base64.b64encode(chunk).decode("ascii")
            message = {"event": "media", "streamSid": stream_sid, "media": {"payload": payload}}
            await websocket.send_text(json.dumps(message))
            await asyncio.sleep(_CHUNK_DURATION_S)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Best-effort: a holding message that fails to play is never worth
        # taking the call down over -- the real pipeline is what matters.
        logger.warning("Holding message playback failed; call continues normally.", exc_info=True)
