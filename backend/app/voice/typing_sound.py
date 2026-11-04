"""A quiet keyboard-typing bed while Mira is looking something up, so the
pause sounds like a receptionist searching on a computer instead of dead air.

Separate from slow_tool_filler.py on purpose: that processor SPEAKS a filler
line after a longer, per-tool delay. This one makes no speech, so it can start
sooner and cover every lookup tool. The two compose -- typing starts, the
spoken filler (if the lookup is still running) takes over the line, and typing
resumes after the filler if the lookup is still not done.

It must never talk over anyone. Typing plays only while ALL of these hold:
  * at least one lookup tool call is pending,
  * the bot is not speaking (TTS started/stopped, bot-speaking frames),
  * the guest is not speaking.
It stops on the first frame that breaks any of them, and on an interruption.
Audio is pushed in short real-time-paced chunks (not the whole clip at once)
so there is never more than one chunk queued ahead of speech.

Sits after tts, right before transport.output(), as plain OutputAudioRawFrame
chunks. A plain OutputAudioRawFrame (not a TTSAudioRawFrame/SpeechOutput
subclass) does not flip the transport's bot-speaking state, so the silence
watchdog and turn logic never mistake typing for Mira talking. The transport
resamples to its own rate, so the same 8 kHz asset serves Exotel, Twilio and
the browser test.

Observation-free and fail-open: if the asset is missing the processor is a
pass-through, and nothing here ever raises into the call path.
"""

import asyncio
import time
import wave
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    FunctionCallCancelFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

_ASSET = Path(__file__).parent / "assets" / "typing_8000.wav"
_SAMPLE_RATE = 8000
_SAMPLE_WIDTH = 2

# Lookup tools only. update_lead is silent bookkeeping, escalate_to_host and
# request_host_transfer speak their own line, end_call is the goodbye, and
# send_* / dispatch_* are actions, not searches.
TYPING_TOOLS = frozenset(
    {
        "search_faq",
        "check_calendar",
        "recommend_properties",
        "get_pricing",
        "negotiate_rate",
        "lookup_booking",
    }
)

# Short enough that a lookup that resolves quickly never makes a sound.
START_DELAY_SECONDS = 0.4
# After a spoken filler ends, resume sooner -- the lookup has already been slow.
RESUME_DELAY_SECONDS = 0.2
CHUNK_SECONDS = 0.04
_CHUNK_BYTES = int(_SAMPLE_RATE * CHUNK_SECONDS) * _SAMPLE_WIDTH


def _load_pcm() -> bytes | None:
    try:
        with wave.open(str(_ASSET), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (_SAMPLE_RATE, 1, _SAMPLE_WIDTH):
                logger.error("typing_8000.wav is not 8000Hz/mono/16-bit -- typing sound disabled")
                return None
            return wav.readframes(wav.getnframes())
    except (FileNotFoundError, wave.Error, OSError) as exc:
        logger.error("typing_8000.wav unreadable ({}) -- typing sound disabled", exc)
        return None


_TYPING_PCM = _load_pcm()


class TypingSoundProcessor(FrameProcessor):
    def __init__(
        self,
        *,
        tools: frozenset[str] = TYPING_TOOLS,
        start_delay: float = START_DELAY_SECONDS,
        resume_delay: float = RESUME_DELAY_SECONDS,
        pcm: bytes | None = _TYPING_PCM,
    ):
        super().__init__()
        self._tools = tools
        self._start_delay = start_delay
        self._resume_delay = resume_delay
        self._pcm = pcm
        self._pending: set[str] = set()
        self._bot_speaking = False
        self._user_speaking = False
        self._task: asyncio.Task | None = None
        # Playback position survives pauses so clicks don't restart at the
        # same spot every time the line goes quiet and returns.
        self._offset = 0
        self._spoke_since_started = False

    # -- state ---------------------------------------------------------

    @property
    def _should_play(self) -> bool:
        return bool(self._pcm) and bool(self._pending) and not self._bot_speaking and not self._user_speaking

    async def _reconcile(self) -> None:
        if self._should_play:
            if self._task is None or self._task.done():
                delay = self._resume_delay if self._spoke_since_started else self._start_delay
                self._task = self.create_task(self._play(delay), "typing_sound")
        else:
            await self._stop()

    async def _stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            await self.cancel_task(task)

    # -- frames --------------------------------------------------------

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        try:
            await self._track(frame)
        except Exception:
            # Never let a sound-effect bug touch the call.
            logger.exception("TypingSoundProcessor: tracking failed -- ignoring")

        await self.push_frame(frame, direction)

    async def _track(self, frame: Frame) -> None:
        if isinstance(frame, FunctionCallsStartedFrame):
            for call in frame.function_calls:
                if call.function_name in self._tools:
                    self._pending.add(call.tool_call_id)
            if not self._bot_speaking:
                self._spoke_since_started = False
            await self._reconcile()
        elif isinstance(frame, (FunctionCallResultFrame, FunctionCallCancelFrame)):
            self._pending.discard(frame.tool_call_id)
            if not self._pending:
                self._spoke_since_started = False
            await self._reconcile()
        elif isinstance(frame, (TTSStartedFrame, TTSAudioRawFrame, BotStartedSpeakingFrame)):
            if not self._bot_speaking:
                self._bot_speaking = True
                self._spoke_since_started = True
                await self._stop()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            await self._reconcile()
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._user_speaking = True
            await self._stop()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._user_speaking = False
            await self._reconcile()
        elif isinstance(frame, InterruptionFrame):
            # The guest cut in: anything queued downstream is dropped by the
            # transport, so just stop feeding it. Whoever was speaking no
            # longer is.
            self._bot_speaking = False
            await self._stop()
        elif isinstance(frame, (EndFrame, CancelFrame)):
            self._pending.clear()
            await self._stop()

    # -- playback ------------------------------------------------------

    async def _play(self, delay: float) -> None:
        pcm = self._pcm
        if not pcm:
            return
        try:
            await asyncio.sleep(delay)
            deadline = time.monotonic()
            while True:
                end = self._offset + _CHUNK_BYTES
                if end <= len(pcm):
                    chunk = pcm[self._offset : end]
                    self._offset = end % len(pcm)
                else:
                    chunk = pcm[self._offset :] + pcm[: end - len(pcm)]
                    self._offset = end - len(pcm)
                await self.push_frame(
                    OutputAudioRawFrame(audio=chunk, sample_rate=_SAMPLE_RATE, num_channels=1)
                )
                deadline += CHUNK_SECONDS
                await asyncio.sleep(max(0.0, deadline - time.monotonic()))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("TypingSoundProcessor: playback failed -- stopping typing sound")
