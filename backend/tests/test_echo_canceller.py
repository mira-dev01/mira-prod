import importlib.util
import math
import random

import numpy as np
import pytest

from pipecat.frames.frames import InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection

from app.voice.echo_canceller import EchoCancellerProcessor, EchoReference, tap_output_transport

SR = 16000


def _speechlike(seconds, seed):
    rng = np.random.default_rng(seed)
    n = int(SR * seconds)
    noise = rng.standard_normal(n)
    # amplitude-modulated band-limited noise: crude stand-in for voiced speech
    env = 0.5 + 0.5 * np.sin(2 * math.pi * 3 * np.arange(n) / SR)
    return (noise * env * 3000).astype(np.int16)


class _Sink(EchoCancellerProcessor):
    def __init__(self, reference):
        super().__init__(reference)
        self.out = bytearray()

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.out += frame.audio


async def _run(proc, reference, far, mic):
    """Feed far-end (bot) audio to the reference in step with mic audio, 20ms at a time."""
    step = SR // 50
    for i in range(0, len(mic), step):
        await reference.feed(far[i : i + step].tobytes(), SR)
        frame = InputAudioRawFrame(audio=mic[i : i + step].tobytes(), sample_rate=SR, num_channels=1)
        await proc.process_frame(frame, FrameDirection.DOWNSTREAM)


def _rms(x):
    x = np.asarray(x, dtype=np.float64)
    return float(np.sqrt(np.mean(x**2))) if len(x) else 0.0


# The real canceller is the optional native `aec-audio-processing` package (Dockerfile installs it
# best-effort; the processor fails open without it). The attenuation test only means anything when
# the package is actually present.
_has_aec = importlib.util.find_spec("aec_audio_processing") is not None


@pytest.mark.skipif(not _has_aec, reason="optional aec-audio-processing package is not installed")
@pytest.mark.asyncio
async def test_pure_echo_is_strongly_attenuated_after_convergence():
    far = _speechlike(8, seed=1)
    delay = int(SR * 0.06)  # 60ms room/network delay
    mic = np.concatenate([np.zeros(delay, np.int16), (far[:-delay] * 0.5).astype(np.int16)])
    reference = EchoReference()
    proc = _Sink(reference)
    # Prime sample rate / APM so the reference accepts audio from the first frame
    await proc.process_frame(InputAudioRawFrame(audio=b"\x00" * 320, sample_rate=SR, num_channels=1), FrameDirection.DOWNSTREAM)
    proc.out.clear()
    await _run(proc, reference, far, mic)
    out = np.frombuffer(bytes(proc.out), dtype=np.int16)
    tail = slice(len(out) // 2, None)  # after the canceller has had time to adapt
    assert _rms(out[tail]) < 0.25 * _rms(mic[: len(out)][tail])


@pytest.mark.asyncio
async def test_near_end_speech_survives_while_echo_is_removed():
    far = _speechlike(8, seed=2)
    near = _speechlike(8, seed=3)
    delay = int(SR * 0.06)
    echo = np.concatenate([np.zeros(delay, np.int16), (far[:-delay] * 0.5).astype(np.int16)])
    mic = np.clip(echo.astype(np.int32) + near.astype(np.int32), -32768, 32767).astype(np.int16)
    reference = EchoReference()
    proc = _Sink(reference)
    await proc.process_frame(InputAudioRawFrame(audio=b"\x00" * 320, sample_rate=SR, num_channels=1), FrameDirection.DOWNSTREAM)
    proc.out.clear()
    await _run(proc, reference, far, mic)
    out = np.frombuffer(bytes(proc.out), dtype=np.int16)
    tail = slice(len(out) // 2, None)
    # What's left should resemble the guest (near-end), not the echo-laden mic.
    n = len(out)
    corr_near = abs(np.corrcoef(out[tail], near[:n][tail])[0, 1])
    corr_echo = abs(np.corrcoef(out[tail], echo[:n][tail])[0, 1])
    assert corr_near > corr_echo


@pytest.mark.asyncio
async def test_silent_bot_leaves_guest_audio_essentially_unchanged():
    near = _speechlike(3, seed=4)
    reference = EchoReference()
    proc = _Sink(reference)
    await proc.process_frame(InputAudioRawFrame(audio=b"\x00" * 320, sample_rate=SR, num_channels=1), FrameDirection.DOWNSTREAM)
    proc.out.clear()
    await _run(proc, reference, np.zeros(len(near), np.int16), near)
    out = np.frombuffer(bytes(proc.out), dtype=np.int16)
    assert _rms(out) > 0.7 * _rms(near[: len(out)])


@pytest.mark.asyncio
async def test_unsupported_sample_rate_passes_through_untouched():
    reference = EchoReference()
    proc = _Sink(reference)
    audio = b"\x01\x00" * 441
    await proc.process_frame(InputAudioRawFrame(audio=audio, sample_rate=44100, num_channels=1), FrameDirection.DOWNSTREAM)
    assert bytes(proc.out) == audio


@pytest.mark.asyncio
async def test_tap_records_audio_at_write_time_and_still_writes():
    reference = EchoReference()
    reference.sample_rate = SR

    class Out:
        written = []

        async def write_audio_frame(self, frame):
            self.written.append(frame)
            return True

    out = Out()
    tap_output_transport(out, reference)

    class F:
        audio = b"\x05\x00" * 160
        sample_rate = SR

    assert await out.write_audio_frame(F()) is True
    assert len(out.written) == 1
    assert reference.take(320) == F.audio
