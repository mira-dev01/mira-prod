"""One-off setup: turns the supplied keyboard-clicking recording into the
loopable "receptionist typing" bed played while Mira is looking something up
(see app/voice/typing_sound.py). Offline, committed as a static WAV, same
approach as generate_ringing_tone.py.

What it does to the source MP3:
  * downmixes to mono and resamples to 8000 Hz (Exotel's native rate, so the
    asset can be loaded and sliced without a resample at playback time),
  * crossfades the tail into the head so the loop point has no click,
  * scales the loudest peak to TARGET_PEAK_DBFS. The source peaks at about
    -2 dBFS, far louder than a background sound should be next to speech.

Usage:
    cd backend && python -m scripts.generate_typing_sound
Needs ffmpeg and numpy on the machine running it (not at runtime).
"""

import subprocess
import wave
from pathlib import Path

import numpy as np

_SAMPLE_RATE = 8000
_ASSETS = Path(__file__).resolve().parent.parent / "app" / "voice" / "assets"
_SOURCE = _ASSETS / "keyboard_clicking_source.mp3"
_OUTPUT = _ASSETS / "typing_8000.wav"

TARGET_PEAK_DBFS = -18.0
_CROSSFADE_SECONDS = 0.25


def main() -> None:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(_SOURCE), "-ac", "1", "-ar", str(_SAMPLE_RATE), "-f", "s16le", "-"],
        check=True,
        capture_output=True,
    ).stdout
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)

    fade = int(_CROSSFADE_SECONDS * _SAMPLE_RATE)
    head, body, tail = samples[:fade], samples[fade:-fade], samples[-fade:]
    ramp = np.linspace(0.0, 1.0, fade)
    blended = tail * (1.0 - ramp) + head * ramp
    looped = np.concatenate([body, blended])

    peak = float(np.max(np.abs(looped)))
    target = 32767 * (10 ** (TARGET_PEAK_DBFS / 20))
    out = np.clip(looped * (target / peak), -32768, 32767).astype(np.int16)

    with wave.open(str(_OUTPUT), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(_SAMPLE_RATE)
        wav.writeframes(out.tobytes())
    print(f"wrote {_OUTPUT} ({len(out) / _SAMPLE_RATE:.2f}s, peak {TARGET_PEAK_DBFS} dBFS)")


if __name__ == "__main__":
    main()
