"""One-off setup: generates the real spoken busy-recovery message played
once, then hung up, when CallCoordinator rejects an incoming call as
BUSY_RECOVERY (see app/voice/ringing_audio.py's play_busy_message and
app/voice/pipeline.py).

The message (app.voice.ringing_audio.BUSY_MESSAGE_TEXT) is 100% static --
no property name, pricing, availability, or guest-specific data is ever
interpolated into it (single call site, app/voice/pipeline.py's
_reject_call_as_busy, always passes the same fixed text). A static message
needs synthesizing exactly once, not on every live call: this script makes
ONE Sarvam TTS request, offline, and commits the result as a static WAV
asset -- the same approach generate_ringing_tone.py and (its predecessor)
generate_busy_message_tone.py already use for the ring tone and the earlier
beep-tone placeholder. This replaces that beep-tone placeholder as
BUSY_RECOVERY's actual audio (see app/voice/ringing_audio.py's
_BUSY_MESSAGE_PCM, now loaded from this script's output instead).

Calling Sarvam live on every busy call (an earlier version of this fix) was
unnecessary network latency and per-call TTS cost for text that never
changes -- baking it once removes both, same reasoning that already applies
to the ring tone.

Voice/model deliberately do NOT match the live pipeline's
settings.sarvam_tts_speaker/settings.sarvam_tts_model (roopa/bulbul:v3,
see app/voice/pipeline.py) -- that combination, even pushed to bulbul:v3's
own pace ceiling (2.0), read as slow and robotic for this specific message
(confirmed live, 2026-08-11: user feedback on real generated samples).
Tried several voice/model/pace combinations as real audio candidates and
picked by ear: bulbul:v2/anushka at pace=1.2 -- v2 has a wider pace range
(0.3-3.0 vs v3's 0.5-2.0) and this specific pairing sounded natural, not
sped up or robotic, unlike v3 at any pace tried. This is a one-off,
one-asset exception, not a proposal to change the live pipeline's own
voice.

Usage:
    cd backend && source venv/bin/activate && python -m scripts.generate_busy_message_speech

Requires SARVAM_API_KEY (reads app.config.settings, same as the rest of the
app). Writes app/voice/assets/busy_message_speech_8000.wav (mono, 16-bit
PCM, 8000 Hz -- Exotel's native rate, requested directly from Sarvam so no
resample step is needed, matching every other committed asset in this
directory).
"""

import asyncio
import wave
from pathlib import Path

import aiohttp

from app.config import settings

_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "app" / "voice" / "assets" / "busy_message_speech_8000.wav"
_SAMPLE_RATE = 8000

# Picked by ear against real generated candidates (see module docstring) --
# deliberately NOT settings.sarvam_tts_model/settings.sarvam_tts_speaker.
_MODEL = "bulbul:v2"
_VOICE = "anushka"
_PACE = 1.2


async def _synthesize() -> bytes:
    from pipecat.services.sarvam.tts import SarvamHttpTTSService

    from app.voice.ringing_audio import BUSY_MESSAGE_TEXT

    if not settings.sarvam_api_key:
        raise RuntimeError("SARVAM_API_KEY is not configured -- cannot synthesize the busy message.")

    async with aiohttp.ClientSession() as http_session:
        tts = SarvamHttpTTSService(
            api_key=settings.sarvam_api_key,
            aiohttp_session=http_session,
            sample_rate=_SAMPLE_RATE,
            settings=SarvamHttpTTSService.Settings(
                model=_MODEL,
                voice=_VOICE,
                language="en-IN",
                pace=_PACE,
                # bulbul:v2-specific params (ignored/warned-on for v3) --
                # explicit defaults, not left to the SDK's own fallback, so
                # this asset's exact synthesis inputs are fully pinned here.
                pitch=0.0,
                loudness=1.0,
            ),
        )
        audio_chunks = []
        async for frame in tts.run_tts(BUSY_MESSAGE_TEXT, context_id="busy-recovery-asset-gen"):
            audio = getattr(frame, "audio", None)
            if audio:
                audio_chunks.append(audio)
        if not audio_chunks:
            raise RuntimeError("Sarvam TTS returned no audio for the busy message.")
        return b"".join(audio_chunks)


def main() -> None:
    pcm = asyncio.run(_synthesize())

    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(_OUTPUT_PATH), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16-bit PCM
        wav_file.setframerate(_SAMPLE_RATE)
        wav_file.writeframes(pcm)

    duration_s = len(pcm) / 2 / _SAMPLE_RATE
    print(f"Wrote {_OUTPUT_PATH} ({duration_s:.2f}s)")


if __name__ == "__main__":
    main()
