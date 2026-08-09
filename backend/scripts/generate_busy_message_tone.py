"""One-off setup: generates the placeholder clip played once, then hung up,
when CallCoordinator rejects an incoming call as BUSY_RECOVERY (see
app/voice/ringing_audio.py's play_busy_message and app/voice/pipeline.py) --
Phase 2 of Busy Call Recovery deliberately stops at "play a placeholder and
hang up," not a real spoken message; see the CallCoordinator integration
plan. Pure synthesis (sine waves), no TTS/API call needed -- generated once,
offline, and committed as a static WAV asset, same approach as
generate_ringing_tone.py.

A distinct triple-beep "fast busy signal" cadence (not the ringback tone's
1s-on/3s-off pattern) -- the standard telephony convention for "this line is
busy," so a real caller who's heard it before recognizes the meaning even as
a placeholder, before it's replaced with a spoken message in a later phase.

Usage:
    cd backend && source venv/bin/activate && python -m scripts.generate_busy_message_tone

Writes app/voice/assets/busy_message_8000.wav (mono, 16-bit PCM, 8000 Hz --
Exotel's native rate, so no resample is needed at playback time; see
ExotelFrameSerializer's output resampler).
"""

import math
import wave
from pathlib import Path

_SAMPLE_RATE = 8000
_TONE_HZ = 480.0  # ITU/Indian fast-busy-signal tone frequency
_ON_SECONDS = 0.25
_OFF_SECONDS = 0.25
_BEEP_COUNT = 3
_AMPLITUDE = 0.25  # headroom below full scale -- avoids clipping on resample

_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "app" / "voice" / "assets" / "busy_message_8000.wav"


def _generate_beep() -> bytes:
    on_samples = int(_SAMPLE_RATE * _ON_SECONDS)
    off_samples = int(_SAMPLE_RATE * _OFF_SECONDS)

    frames = bytearray()
    for i in range(on_samples):
        # Short fade in/out (5ms), same reasoning as generate_ringing_tone.py:
        # avoids an audible click/pop at the tone's start/stop edges.
        fade_samples = int(_SAMPLE_RATE * 0.005)
        fade = 1.0
        if i < fade_samples:
            fade = i / fade_samples
        elif i > on_samples - fade_samples:
            fade = (on_samples - i) / fade_samples
        value = _AMPLITUDE * fade * math.sin(2 * math.pi * _TONE_HZ * i / _SAMPLE_RATE)
        sample = int(value * 32767)
        frames += sample.to_bytes(2, byteorder="little", signed=True)

    frames += bytes(off_samples * 2)  # silence, 16-bit PCM = 2 bytes/sample
    return bytes(frames)


def main() -> None:
    beep = _generate_beep()
    pcm = beep * _BEEP_COUNT

    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(_OUTPUT_PATH), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16-bit PCM
        wav_file.setframerate(_SAMPLE_RATE)
        wav_file.writeframes(pcm)

    duration_s = len(pcm) / 2 / _SAMPLE_RATE
    print(f"Wrote {_OUTPUT_PATH} ({duration_s:.2f}s, {_BEEP_COUNT} beeps, played once)")


if __name__ == "__main__":
    main()
