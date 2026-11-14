import array

import pytest

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    OutputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.tests.utils import SleepFrame, run_test

from app.voice.barge_in_ducking import BargeInDuckingProcessor, scale_pcm16


def _pcm(*samples):
    return array.array("h", samples).tobytes()


def test_scale_pcm16_halves_amplitude_and_clips():
    out = array.array("h")
    out.frombytes(scale_pcm16(_pcm(1000, -1000, 32767), 0.5))
    assert list(out) == [500, -500, 16383]


def test_scale_pcm16_unity_gain_is_untouched():
    data = _pcm(1, 2, 3)
    assert scale_pcm16(data, 1.0) is data


class _Probe(BargeInDuckingProcessor):
    def __init__(self, now):
        super().__init__(duck_gain=0.5, now=now)
        self.pushed = []

    async def process_frame(self, frame, direction):
        # bypass FrameProcessor's pipeline plumbing; exercise only our logic
        try:
            if isinstance(frame, VADUserStartedSpeakingFrame):
                self._ducked_at = self._now()
            elif isinstance(frame, (VADUserStoppedSpeakingFrame, BotStoppedSpeakingFrame)):
                self._ducked_at = None
            elif isinstance(frame, OutputAudioRawFrame) and self._is_ducked():
                frame.audio = scale_pcm16(frame.audio, self._duck_gain)
        finally:
            self.pushed.append(frame)


def _audio():
    return OutputAudioRawFrame(audio=_pcm(1000, 1000), sample_rate=8000, num_channels=1)


@pytest.mark.asyncio
async def test_ducks_after_vad_start_and_restores_after_stop():
    now = [0.0]
    p = _Probe(lambda: now[0])
    await p.process_frame(VADUserStartedSpeakingFrame(start_secs=0.35, timestamp=0), FrameDirection.DOWNSTREAM)
    ducked = _audio()
    await p.process_frame(ducked, FrameDirection.DOWNSTREAM)
    assert list(array.array("h", ducked.audio)) == [500, 500]

    await p.process_frame(VADUserStoppedSpeakingFrame(stop_secs=0.2, timestamp=0), FrameDirection.DOWNSTREAM)
    normal = _audio()
    await p.process_frame(normal, FrameDirection.DOWNSTREAM)
    assert list(array.array("h", normal.audio)) == [1000, 1000]


@pytest.mark.asyncio
async def test_never_stays_ducked_if_stop_signal_is_lost():
    now = [0.0]
    p = _Probe(lambda: now[0])
    await p.process_frame(VADUserStartedSpeakingFrame(start_secs=0.35, timestamp=0), FrameDirection.DOWNSTREAM)
    now[0] = 10.0
    frame = _audio()
    await p.process_frame(frame, FrameDirection.DOWNSTREAM)
    assert list(array.array("h", frame.audio)) == [1000, 1000]


@pytest.mark.asyncio
async def test_ducks_through_real_pipecat_setup():
    """The tests above call process_frame on a subclass that skips FrameProcessor's
    setup, so they could not see pipecat replacing a processor's own attributes at
    StartFrame. This one runs the real processor in a real pipeline: it ducked
    nothing (and logged barge_in_ducking_failed) while the timer shared a name with
    pipecat's clock."""
    processor = BargeInDuckingProcessor(duck_gain=0.5)
    audio = _audio()

    down_frames, _ = await run_test(
        processor,
        frames_to_send=[
            VADUserStartedSpeakingFrame(start_secs=0.35, timestamp=0),
            SleepFrame(sleep=0.05),
            audio,
        ],
    )

    out = [f for f in down_frames if isinstance(f, OutputAudioRawFrame)]
    assert len(out) == 1
    assert list(array.array("h", out[0].audio)) == [500, 500]
