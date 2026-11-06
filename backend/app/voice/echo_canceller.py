"""Server-side acoustic echo cancellation for phone calls.

When a guest is on speakerphone, Mira's own voice leaks back into their mic and
arrives as "user speech" -- which VAD hears and uses to interrupt Mira with
herself. A full-duplex agent has to listen while it speaks, so we subtract
Mira's outgoing audio from the incoming audio using WebRTC's AEC (the
`aec-audio-processing` package, same engine browsers use).

Two parts sharing one EchoReference:

- `tap_output_transport` wraps the transport's `write_audio_frame` so every
  chunk Mira actually sends is recorded at playout time. This is deliberately
  NOT a pipeline processor after `tts`: TTS produces audio far faster than
  real time and the transport paces it out, so a tap after `tts` would run
  seconds ahead of what the guest hears and AEC could never line it up.
- `EchoCancellerProcessor` sits right after `transport.input()` and cleans each
  10ms of guest audio against the matching reference.

Only AEC is enabled (noise suppression and gain control stay off) so what
Sarvam hears is the guest's audio minus echo and nothing else. Fails open: any
error forwards the original audio untouched and disables itself for the call.

Gated by settings.echo_cancellation_enabled.
"""

from loguru import logger

from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import Frame, InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Never let the reference buffer grow past this; if mic frames stop arriving
# we'd otherwise accumulate a stale tail that mis-aligns the next utterance.
_MAX_REFERENCE_SECS = 1.0
_FRAME_MS = 10


class EchoReference:
    """Mira's recent outgoing audio, held at the mic's sample rate."""

    def __init__(self):
        # Set by EchoCancellerProcessor from the first mic frame (the pipeline's
        # input rate isn't known until the call starts); until then feed() drops.
        self.sample_rate: int | None = None
        self._buf = bytearray()
        self._resampler = create_stream_resampler()

    async def feed(self, audio: bytes, sample_rate: int):
        if self.sample_rate is None:
            return
        if sample_rate != self.sample_rate:
            audio = await self._resampler.resample(audio, sample_rate, self.sample_rate)
        self._buf += audio
        limit = int(self.sample_rate * 2 * _MAX_REFERENCE_SECS)
        if len(self._buf) > limit:
            del self._buf[: len(self._buf) - limit]

    def take(self, nbytes: int) -> bytes:
        """Exactly `nbytes` of reference, zero-padded when Mira is silent."""
        chunk = bytes(self._buf[:nbytes])
        del self._buf[:nbytes]
        return chunk + b"\x00" * (nbytes - len(chunk))


def tap_output_transport(output_transport, reference: EchoReference) -> None:
    """Record every audio chunk the transport writes, at the moment it writes it."""
    original = output_transport.write_audio_frame

    async def write_audio_frame(frame):
        try:
            await reference.feed(frame.audio, frame.sample_rate)
        except Exception:
            logger.exception("echo_reference_feed_failed")
        return await original(frame)

    output_transport.write_audio_frame = write_audio_frame


class EchoCancellerProcessor(FrameProcessor):
    def __init__(self, reference: EchoReference):
        super().__init__()
        self._reference = reference
        self._apm = None
        self._pending = b""
        self._disabled = False

    def _ensure_apm(self, sr: int):
        if self._apm is None:
            from aec_audio_processing import AudioProcessor

            self._reference.sample_rate = sr
            apm = AudioProcessor(enable_aec=True, enable_ns=False, enable_agc=False, enable_vad=False)
            apm.set_stream_format(sr, 1, sr, 1)
            apm.set_reverse_stream_format(sr, 1)
            self._apm = apm
        return self._apm

    async def _clean(self, audio: bytes, sample_rate: int) -> bytes:
        apm = self._ensure_apm(sample_rate)
        chunk_bytes = sample_rate // 1000 * _FRAME_MS * 2
        data = self._pending + audio
        out = bytearray()
        pos = 0
        while len(data) - pos >= chunk_bytes:
            mic = data[pos : pos + chunk_bytes]
            pos += chunk_bytes
            apm.process_reverse_stream(self._reference.take(chunk_bytes))
            out += apm.process_stream(bytes(mic))
        self._pending = data[pos:]
        return bytes(out)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if (
            isinstance(frame, InputAudioRawFrame)
            and direction == FrameDirection.DOWNSTREAM
            and not self._disabled
            and frame.num_channels == 1
            and frame.sample_rate in (8000, 16000, 32000, 48000)
            and self._reference.sample_rate in (None, frame.sample_rate)
        ):
            try:
                cleaned = await self._clean(frame.audio, frame.sample_rate)
                if cleaned:
                    frame = InputAudioRawFrame(audio=cleaned, sample_rate=frame.sample_rate, num_channels=1)
                else:
                    return  # less than 10ms accumulated; carried into the next frame
            except Exception:
                logger.exception("echo_canceller_failed_disabling_for_call")
                self._disabled = True
        await self.push_frame(frame, direction)
