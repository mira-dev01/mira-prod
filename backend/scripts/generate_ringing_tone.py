"""One-off setup: generates the phone-ringing tone played on the raw Exotel
WebSocket while a call's DB lookups, pipeline build, and Sarvam STT/TTS
connections are still in progress (see app/voice/ringing_audio.py) -- the
same ~4-6s of unavoidable setup the earlier holding-message feature covered,
now filled with a standard ringback tone instead of a spoken line. Pure
synthesis (sine waves), no TTS/API call needed -- generated once, offline,
and committed as a static WAV asset.

Standard Indian/ITU-style ringback cadence: 400Hz continuous tone,
1s on / 3s off, repeating. The clip below is exactly one full cycle (4s);
app/voice/ringing_audio.py loops this clip's raw PCM bytes indefinitely
until cancelled, so the cadence continues seamlessly for as long as setup
takes.

Usage:
    cd backend && source venv/bin/activate && python -m scripts.generate_ringing_tone

Writes app/voice/assets/ringing_tone_8000.wav (mono, 16-bit PCM, 8000 Hz --
Exotel's native rate, so no resample is needed at playback time; see
ExotelFrameSerializer's output resampler).
"""

import math
import wave
from pathlib import Path

_SAMPLE_RATE = 8000
_TONE_HZ = 400.0
_ON_SECONDS = 1.0
_OFF_SECONDS = 3.0
_AMPLITUDE = 0.25  # headroom below full scale -- avoids clipping on resample

_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "app" / "voice" / "assets" / "ringing_tone_8000.wav"


def _generate_cycle() -> bytes:
    on_samples = int(_SAMPLE_RATE * _ON_SECONDS)
    off_samples = int(_SAMPLE_RATE * _OFF_SECONDS)

    frames = bytearray()
    for i in range(on_samples):
        # Short fade in/out (5ms) on the tone itself so the loop point and
        # the on->off transition never produce an audible click/pop --
        # a hard-edged sine start/stop is the classic cause of that.
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
    pcm = _generate_cycle()

    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(_OUTPUT_PATH), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16-bit PCM
        wav_file.setframerate(_SAMPLE_RATE)
        wav_file.writeframes(pcm)

    duration_s = len(pcm) / 2 / _SAMPLE_RATE
    print(f"Wrote {_OUTPUT_PATH} ({duration_s:.2f}s, one full ring cycle)")


if __name__ == "__main__":
    main()
