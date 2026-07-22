"""One-off setup: generates the "connecting you now" holding-message audio
played on the raw Exotel WebSocket while a call's DB lookups, pipeline
build, and Sarvam STT/TTS connections are still in progress (see
app/voice/holding_audio.py) -- confirmed via call logs that this setup
takes ~4-5s, which the guest otherwise hears as dead air before Mira's
first word. Generated once, offline, via Sarvam's plain HTTP TTS endpoint
(not the live websocket pipeline) and committed as a static WAV asset --
real calls never call Sarvam for this, they just play back the file.

Usage:
    cd backend && source venv/bin/activate && python -m scripts.generate_holding_message

Writes app/voice/assets/holding_message_8000.wav (mono, 16-bit PCM,
8000 Hz -- Exotel's native rate, so no resample is needed at playback
time; see ExotelFrameSerializer.InputParams.exotel_sample_rate).
"""

import asyncio
import base64
import wave
from pathlib import Path

import aiohttp

from app.config import settings

# Kept short deliberately: at bulbul:v3's natural cadence even a modest
# sentence runs long (10 words measured at ~12.5s at pace=1.0) -- this text
# at pace=1.3 (below) lands at ~4.7s, matching the ~4-5s of setup
# (DB lookups, pipeline build, Sarvam STT/TTS connect) it's meant to cover.
_TEXT = "Welcome to Mira AI. Connecting you now."

_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "app" / "voice" / "assets" / "holding_message_8000.wav"


async def main() -> None:
    payload = {
        "text": _TEXT,
        "target_language_code": "en-IN",
        "speaker": settings.sarvam_tts_speaker,
        "sample_rate": 8000,
        "model": settings.sarvam_tts_model,
        "pace": 1.3,
        "enable_preprocessing": True,
    }
    headers = {
        "api-subscription-key": settings.sarvam_api_key,
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.sarvam.ai/text-to-speech", json=payload, headers=headers
        ) as response:
            response.raise_for_status()
            data = await response.json()

    audio_bytes = base64.b64decode(data["audios"][0])
    # Sarvam's HTTP endpoint returns a full WAV (RIFF header + PCM data) --
    # strip the header so we can re-wrap it with `wave` below and be certain
    # of the exact format on disk, rather than trusting Sarvam's header verbatim.
    if audio_bytes.startswith(b"RIFF"):
        audio_bytes = audio_bytes[44:]

    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(_OUTPUT_PATH), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16-bit PCM
        wav_file.setframerate(8000)
        wav_file.writeframes(audio_bytes)

    duration_s = len(audio_bytes) / 2 / 8000
    print(f"Wrote {_OUTPUT_PATH} ({duration_s:.2f}s)")


if __name__ == "__main__":
    asyncio.run(main())
