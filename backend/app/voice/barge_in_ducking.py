"""Briefly lowers Mira's volume while the guest is talking over her, instead of
leaving her at full volume until a barge-in is confirmed (or never, for an
acknowledgement). Together with BackchannelAwareUserTurnStartStrategy this
gives phone calls the overlap behavior people expect: a "hmm" dips Mira for a
moment and she carries on; real speech cuts her off entirely.

Sits right after `tts`, scaling only audio not yet sent to the transport (a
frame or two of PCM), so the dip lands within ~20-40ms of VAD firing. Purely
an audio-level effect -- it never touches text, tool calls or turn state, and
fails open (the original frame is forwarded if anything goes wrong).

Gated by settings.barge_in_ducking_enabled.
"""

import array
import time

from loguru import logger

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    OutputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# If a stop signal is ever lost, never leave Mira ducked longer than this.
_MAX_DUCK_SECS = 2.5


def scale_pcm16(audio: bytes, gain: float) -> bytes:
    """Scale little-endian 16-bit PCM by `gain` (0..1), clipping safely."""
    if gain >= 1.0 or not audio:
        return audio
    samples = array.array("h")
    samples.frombytes(audio[: len(audio) - (len(audio) % 2)])
    for i, s in enumerate(samples):
        samples[i] = max(-32768, min(32767, int(s * gain)))
    return samples.tobytes()


class BargeInDuckingProcessor(FrameProcessor):
    def __init__(self, *, duck_gain: float = 0.3, now=time.monotonic):
        super().__init__()
        self._duck_gain = duck_gain
        # Deliberately not named after pipecat's own clock attribute: FrameProcessor replaces
        # that one with a clock object at setup, so calling it raised TypeError on every frame.
        self._now = now
        self._ducked_at: float | None = None

    def _is_ducked(self) -> bool:
        if self._ducked_at is None:
            return False
        if self._now() - self._ducked_at > _MAX_DUCK_SECS:
            self._ducked_at = None
            return False
        return True

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        try:
            if isinstance(frame, VADUserStartedSpeakingFrame):
                self._ducked_at = self._now()
            elif isinstance(
                frame, (VADUserStoppedSpeakingFrame, InterruptionFrame, BotStoppedSpeakingFrame)
            ):
                self._ducked_at = None
            elif (
                isinstance(frame, OutputAudioRawFrame)
                and direction == FrameDirection.DOWNSTREAM
                and self._is_ducked()
            ):
                frame.audio = scale_pcm16(frame.audio, self._duck_gain)
        except Exception:
            logger.exception("barge_in_ducking_failed")
        await self.push_frame(frame, direction)
