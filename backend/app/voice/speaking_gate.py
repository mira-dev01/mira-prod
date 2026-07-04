"""BotSpeakingGate: shared by app/voice/pipeline.py (which places it in the
Pipeline) and app/voice/tools.py (whose DB-touching tools await it) -- lives
in its own module so neither of those two needs to import the other.
"""

import asyncio

from pipecat.frames.frames import BotStartedSpeakingFrame, BotStoppedSpeakingFrame, Frame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class BotSpeakingGate(FrameProcessor):
    """Tracks real bot-speaking state by watching BotStartedSpeakingFrame /
    BotStoppedSpeakingFrame as they flow back upstream from transport.output()
    -- these fire on actual audio playback state, not on frame *queuing*
    (queue_frame() returns as soon as a frame is enqueued, well before its
    audio reaches the speaker). Placed immediately after transport.output()
    in the pipeline so it observes the real signal at its source.

    Exists so a tool handler that queues a filler TTSSpeakFrame (see
    app/voice/tools.py's `_speak_filler_and_start_hold_music`) can await the
    *actual* playback of that phrase finishing before enabling hold music --
    without this, hold music could start mixing while the filler phrase is
    still being spoken, which is exactly the overlap this feature must not
    have.

    Also owns `hold_music_lock`: pipecat's LLM services run same-turn
    function calls concurrently by default (run_in_parallel=True), and the
    system prompt explicitly tells the model to call update_lead "silently
    ... every time you learn a new field," which can easily co-occur with
    check_calendar/get_pricing in the same turn (e.g. the guest gives their
    name and dates together). Without a lock, two tools racing on the same
    mixer/gate could talk over each other's filler phrase or turn hold music
    off while the other tool is still waiting on it. Tool functions hold
    this lock for their whole filler-phrase-through-music-off span, so
    concurrent DB-touching tool calls queue up cleanly instead of
    interleaving.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._speaking_started = asyncio.Event()
        self._speaking_stopped = asyncio.Event()
        self._speaking_stopped.set()  # idle at call start
        self.hold_music_lock = asyncio.Lock()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._speaking_stopped.clear()
            self._speaking_started.set()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._speaking_started.clear()
            self._speaking_stopped.set()
        await self.push_frame(frame, direction)

    async def wait_for_utterance_to_finish(self) -> None:
        """Wait for the bot to start speaking, then wait for it to stop.
        Call this right after queuing a TTSSpeakFrame to block until that
        specific phrase has actually finished playing."""
        self._speaking_started.clear()
        await self._speaking_started.wait()
        await self._speaking_stopped.wait()
