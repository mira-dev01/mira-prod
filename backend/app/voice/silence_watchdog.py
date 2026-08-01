"""Ends a call when the guest goes silent/unresponsive for too long, or when
the LLM itself has signaled the conversation reached a natural close.

Nothing in the pipeline previously tracked elapsed silence at all -- STT still
emits a TranscriptionFrame for any VAD-detected segment even when there's no
real speech in it (ambient noise, a mic click, breathing), just with empty or
near-empty text. Confirmed live on 2026-07-20: after the greeting, 15s of true
silence produced no reaction whatsoever, then a stray noise segment happened
to transcribe as "No" and the LLM answered it as if the guest had spoken --
with nothing else in place, that cycle can repeat indefinitely and the call
never ends on its own.

Nor did anything end a call that finished normally -- a guest who says "no
that's all, thanks" got a spoken closing line (system_prompt.py's Closing
phrasing rule + the end_call tool, app/voice/tools.py) but the call itself
just sat open afterwards with nothing driving it to hang up, relying purely
on the guest hanging up their end.

SilenceWatchdogProcessor sits between stt and language_sync (see
app/voice/pipeline.py) so it sees every TranscriptionFrame plus the upstream
copy of BotStoppedSpeakingFrame that base_output.py always pushes once TTS
audio finishes draining. It ignores blank/whitespace-only transcripts
entirely (they never reset the timer or count as a real reply), nudges the
guest once per timeout with a spoken prompt, and ends the call after the
second consecutive nudge goes unanswered.

request_end_after_current_turn() is the other entry point: the end_call tool
calls it the moment the LLM commits to closing the call (after speaking its
own closing line as normal LLM text, same pattern as escalate_to_host's
escalation phrasing). It doesn't end the call immediately -- the tool has no
way to know whether TTS has actually finished speaking that line yet -- it
just arms a flag so the *next* BotStoppedSpeakingFrame (i.e. once that
closing line has actually finished playing) ends the call instead of
starting another silence-nudge cycle.

Phase 5 (documentation/agent-conversation-improvement.md), conversation
lifecycle: this processor already owns every transition a real "closing"
state needs to hang off of -- request_end_after_current_turn() (armed),
cancel_end_request()/a real guest TranscriptionFrame arriving while armed
(reopened), and the actual EndWorkerFrame push (closed) -- so
ConversationState.closing_state (declared in Phase 1.1) is threaded through
here rather than adding a parallel tracking mechanism. This is state for the
PROMPT layer (StatePromptSyncProcessor tells the model "a goodbye has
already been delivered, don't re-open new topics unless the guest does"),
distinct from this processor's own hangup_pending property above, which
RedundantContextGuardProcessor reads for a different purpose (suppressing a
spurious re-invocation).
"""

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
    Frame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

if TYPE_CHECKING:
    from app.voice.conversation_state import ConversationState

DEFAULT_SILENCE_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_PROMPTS = 2
# Two distinct nudges, not the same line twice -- a human receptionist
# doesn't repeat themselves verbatim (see GOLDEN_RULES's own "never repeat a
# sentence" rule, same underlying reasoning applied here).
DEFAULT_PROMPT_TEXTS = ["Hello?", "Hello, are you there?"]
DEFAULT_GOODBYE_TEXT = "I'll go ahead and end the call here. Feel free to call back anytime -- have a great day!"


class SilenceWatchdogProcessor(FrameProcessor):
    """Nudges, then hangs up on, a guest who's gone silent."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_SILENCE_TIMEOUT_SECONDS,
        max_prompts: int = DEFAULT_MAX_PROMPTS,
        prompt_texts: list[str] = DEFAULT_PROMPT_TEXTS,
        goodbye_text: str = DEFAULT_GOODBYE_TEXT,
        conversation_state: "ConversationState | None" = None,
    ):
        super().__init__()
        self._timeout_seconds = timeout_seconds
        self._max_prompts = max_prompts
        self._prompt_texts = prompt_texts
        self._goodbye_text = goodbye_text
        self._conversation_state = conversation_state

        self._prompts_sent = 0
        self._timer_task: asyncio.Task | None = None
        self._ended = False
        self._end_requested = False

    @property
    def hangup_pending(self) -> bool:
        """True once a hangup has been armed (request_end_after_current_turn)
        or already fired. Used by RedundantContextGuardProcessor (app/voice/
        redundant_context_guard.py) to drop a spurious LLM re-invocation that
        would otherwise race the actual hangup -- see that module's docstring
        for the failure mode this closes."""
        return self._end_requested or self._ended

    async def request_end_after_current_turn(self) -> None:
        """Called by the end_call tool (app/voice/tools.py) once the LLM has
        committed to closing the call and spoken its own closing line as
        normal text (system_prompt.py's Closing phrasing rule -- same pattern
        as escalate_to_host's escalation phrasing). Cancels any in-flight
        silence-nudge timer so it can't fire concurrently with (or instead
        of) the close, and arms _end_requested so the next
        BotStoppedSpeakingFrame -- i.e. once that closing line has actually
        finished playing, not when this method is called -- ends the call
        instead of starting another nudge cycle."""
        self._end_requested = True
        if self._conversation_state is not None:
            self._conversation_state.mark_farewell_pending()
        await self._cancel_timer()

    def cancel_end_request(self) -> None:
        """Called by PrematureEndCallGuardProcessor (app/voice/
        premature_end_call_guard.py) when the same turn that called end_call
        also asked the guest an open question -- confirmed live 2026-07-26:
        "Anything else you'd like to sort out? Thanks so much for calling --
        have a wonderful day!" and end_call fired together in one turn, never
        giving the guest a real chance to answer. Un-arms the pending
        end-of-call so the next BotStoppedSpeakingFrame falls through to the
        normal silence-nudge/restart-timer path instead of hanging up --
        the guest still gets nudged and the call still ends eventually if
        they truly say nothing, just not instantly."""
        self._end_requested = False
        if self._conversation_state is not None:
            self._conversation_state.mark_reopened()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStoppedSpeakingFrame) and not self._ended:
            if self._end_requested:
                self._ended = True
                logger.info("SilenceWatchdogProcessor: ending call after agent's closing line")
                if self._conversation_state is not None:
                    self._conversation_state.mark_closed()
                await self.push_frame(EndWorkerFrame(reason="conversation complete"))
            else:
                # The bot just finished a turn (greeting or a real reply) --
                # start (or restart) the silence clock from here rather than
                # from call start, so a guest who's mid-conversation but just
                # slow to reply isn't penalized differently from one who
                # never says anything. This processor's own nudge/goodbye
                # restart the timer directly in _on_timeout instead of
                # relying on this frame coming back around -- see the
                # comment there.
                await self._restart_timer()
        elif isinstance(frame, TranscriptionFrame):
            if frame.text and frame.text.strip():
                # A real transcript -- the guest is actually there. Reset the
                # strike count and let the normal turn-taking flow continue;
                # the timer will restart on the next BotStoppedSpeakingFrame
                # once the agent replies. Also cancels a pending end-of-call
                # request: if the guest speaks again while/after the agent's
                # closing line is still playing (e.g. they thought of one
                # more question), that's a clear sign the call isn't actually
                # over -- don't hang up on top of them.
                self._prompts_sent = 0
                if self._end_requested and self._conversation_state is not None:
                    self._conversation_state.mark_reopened()
                self._end_requested = False
                await self._cancel_timer()
            # Blank/whitespace-only transcripts (background noise, breathing)
            # are exactly the false-positive case that caused an unprompted
            # reply live -- swallow them here too so they can't reset the
            # timer or be mistaken for a real reply.  They're still forwarded
            # downstream unchanged below since other processors may care
            # (e.g. the turn-stop strategy's own empty-transcript handling).

        await self.push_frame(frame, direction)

    async def _restart_timer(self):
        await self._cancel_timer()
        self._timer_task = self.create_task(self._on_timeout(), "silence_watchdog_timer")

    async def _cancel_timer(self):
        if self._timer_task:
            await self.cancel_task(self._timer_task)
            self._timer_task = None

    async def _on_timeout(self):
        try:
            await asyncio.sleep(self._timeout_seconds)
        except asyncio.CancelledError:
            return
        finally:
            self._timer_task = None

        if self._ended:
            return

        self._prompts_sent += 1
        if self._prompts_sent > self._max_prompts:
            self._ended = True
            logger.info(
                "SilenceWatchdogProcessor: guest unresponsive after {} prompts, ending call",
                self._max_prompts,
            )
            await self.push_frame(TTSSpeakFrame(self._goodbye_text, append_to_context=False))
            # A processor can't just push EndFrame downstream itself and
            # expect the pipeline worker to notice -- PipelineWorker only
            # treats the pipeline as finished when its own push-queue loop
            # dequeues a terminal frame it queued, and that loop has no way
            # to observe frames injected mid-pipeline. EndWorkerFrame pushed
            # downstream (pipecat's own documented pattern -- see its
            # docstring) reaches the sink, gets bounced upstream to the
            # source, and *that* is what makes PipelineWorker.queue_frame an
            # EndFrame the push-queue loop actually sees and can end on.
            await self.push_frame(EndWorkerFrame(reason="silent caller"))
            return

        logger.info(
            "SilenceWatchdogProcessor: {}s of silence, sending prompt {}/{}",
            self._timeout_seconds,
            self._prompts_sent,
            self._max_prompts,
        )
        # index by which nudge this is (1st -> "Hello?", 2nd -> "Hello, are
        # you there?"); clamp to the last entry if max_prompts ever exceeds
        # how many distinct lines are configured.
        prompt_index = min(self._prompts_sent, len(self._prompt_texts)) - 1
        await self.push_frame(TTSSpeakFrame(self._prompt_texts[prompt_index], append_to_context=False))
        # Restart the clock directly rather than waiting for TTS to actually
        # speak the nudge and transport to report BotStoppedSpeakingFrame --
        # that round trip works too (this frame will still arrive and no-op
        # into a timer restart) but makes the next prompt's timing depend on
        # TTS speed, and races against pipeline teardown once EndFrame is
        # pushed. Restarting here instead makes each prompt exactly
        # timeout_seconds apart regardless of TTS latency.
        await self._restart_timer()
