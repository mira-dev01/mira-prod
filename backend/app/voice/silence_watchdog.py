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
audio finishes draining.

Phase 5D (documentation/agent-conversation-improvement.md) rewrote what
"the guest responded" actually means. Phases 5A-5C established that a plain
non-blank TranscriptionFrame is too weak a signal -- Exotel's mono audio
means a background/bystander utterance produces a TranscriptionFrame
indistinguishable, at that layer, from a genuine guest reply, so resetting
the nudge cycle on any transcript let a background-noisy, truly-unresponsive
call defer its own hangup indefinitely (Phase 5A/5C's own characterization
tests). Phase 5D found the fix was NOT a new classifier or a shorter timeout,
but a WIRING BUG: pipecat 1.6.0 moved `vad_analyzer` from `TransportParams`
to `LLMUserAggregatorParams`, and this codebase was still passing it to the
transport, where Pydantic's default extra="ignore" silently dropped it with
no error -- local VAD-driven turn detection was dead in the live pipeline
before this phase (see app/voice/pipeline.py's _VAD_PARAMS comment for the
full story). With that fixed, pipecat's own LLMUserAggregator broadcasts two
much stronger, already-built signals UPSTREAM to this processor's position:

- UserStartedSpeakingFrame: fires the moment VAD confirms the guest began
  speaking (confirmed directly by tracing pipecat's own
  VADUserTurnStartStrategy/TranscriptionUserTurnStartStrategy). Used here to
  CANCEL the pending nudge timer the instant the guest starts talking, so a
  nudge can never fire mid-utterance and interrupt them (Phase 5D's own
  "guest actively speaking" requirement) -- confirmed empirically during this
  phase's investigation that this frame reaches this processor's pipeline
  position once the VAD wiring bug above is fixed.
- UserStoppedSpeakingFrame: fires ONLY once pipecat's own turn-stop strategy
  (SpeechTimeoutUserTurnStopStrategy, wait_for_transcript=True by default)
  has confirmed BOTH that VAD detected the guest stop speaking AND that a
  non-empty transcript actually arrived for that utterance -- confirmed
  directly, by tracing pipecat's own aggregator source, that a pure VAD-stop
  with zero transcript (ambient noise, a mic click, breathing) never
  produces this frame at all. This is the real "meaningful guest turn
  completed" boundary Phase 5D asked for -- structurally stronger than "any
  non-blank TranscriptionFrame", and reused from pipecat's own machinery
  rather than reinvented here.

The strike counter (_prompts_sent) now resets ONLY on UserStoppedSpeakingFrame
(a genuinely completed turn), not on raw TranscriptionFrame -- and even then,
only when the completed turn's text does NOT look like a repeated/background
pattern per the repetition-shadow signal (Phase 5C, graduated from
observation-only to load-bearing in this phase -- see _observe_repetition_
shadow's own docstring for exactly what it does and does not claim). This is
what satisfies Phase 5D's "irrelevant/background input must not indefinitely
reset the watchdog" requirement without a new classifier, an LLM call, or a
length/word-count heuristic (all explicitly ruled out) -- reusing the same
deterministic, already-tested signal Phase 5C built and intentionally left
inert pending exactly this kind of explicit, evidenced product decision.
TranscriptionFrame itself is still handled (feeds the repetition-shadow
observation, and still cancels a pending end-of-call request the moment ANY
transcript arrives -- deliberately kept on the earliest possible signal for
that one narrow case, not gated on full turn completion, so a guest who
speaks up while a closing line is still playing is never hung up on top of),
but no longer resets the strike counter or the timer on its own.

It nudges the guest once per timeout with a spoken prompt, and ends the call
after the second consecutive nudge goes unanswered -- unchanged from Phase
5A/5C, just re-gated on the stronger signal above. Both the nudge lines and
the goodbye line are now spoken WITH append_to_context=True (previously
False) so they appear on the calls-page transcript like any other spoken
line -- see _on_timeout's own comments for the accepted consecutive-
assistant-role risk this carries on a non-Groq llm_provider.

Post-Phase-5D live-call finding: Phase 5D's own state model listed
BOT_SPEAKING as a state but never actually gated the timer on it --
BotStartedSpeakingFrame was never watched here at all, only
BotStoppedSpeakingFrame. Confirmed live: this let the countdown keep
running while the bot was still generating/speaking a genuine reply, or
while a slow tool call was still in flight (see slow_tool_filler.py's own
documented ~30s worst case for recommend_properties/the SearchApi.io
pricing path) -- a nudge could fire mid-answer ("hello? hello?" right after
the bot finished talking), and in the worst case enough nudge cycles could
elapse to hang up on a guest who was still waiting for an answer they'd
never had a chance to respond to, since nothing resets _prompts_sent except
a completed GUEST turn. Fixed by also cancelling the timer on
BotStartedSpeakingFrame, symmetric with the existing UserStartedSpeakingFrame
handling below -- confirmed via pipecat's own frame docstrings that
BotStartedSpeakingFrame, like BotStoppedSpeakingFrame, is broadcast BOTH
upstream and downstream by the transport layer, so it reaches this
processor's position with no new wiring required (the same mechanism
BotStoppedSpeakingFrame already relied on). This also means slow_tool_
filler's own filler line (a real TTSSpeakFrame, once actually spoken)
now correctly pauses/resumes this timer too, for free -- not a coincidence,
the two modules were built on top of the same transport-level frame pair.

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
import time
from collections import deque
from typing import TYPE_CHECKING

from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
    Frame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.voice.repetition_guard import _normalize, _word_overlap

if TYPE_CHECKING:
    from app.voice.conversation_state import ConversationState

DEFAULT_SILENCE_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_PROMPTS = 2
# Two distinct nudges, not the same line twice -- a human receptionist
# doesn't repeat themselves verbatim (see GOLDEN_RULES's own "never repeat a
# sentence" rule, same underlying reasoning applied here).
DEFAULT_PROMPT_TEXTS = ["Hello?", "Hello, are you there?"]
DEFAULT_GOODBYE_TEXT = "I'll go ahead and end the call here. Feel free to call back anytime -- have a great day!"

# ---------------------------------------------------------------------------
# Phase 5C built this signal in SHADOW MODE (observation/logging only).
# Phase 5D (documentation/agent-conversation-improvement.md) is the explicit,
# evidenced product decision Phase 5C's own report said was required before
# graduating it -- these values now DO gate real behavior (see
# process_frame's own UserStoppedSpeakingFrame branch below): a completed
# turn whose text matches this signal does not reset the nudge strike
# counter, though the timer is
# still restarted (the guest still gets another full window -- this only
# stops repeated/background-shaped turns from resetting the 2-follow-up
# cap indefinitely, it never shortens anyone's response time).
#
# STILL EXPERIMENTAL THRESHOLDS -- not validated against real call data.
# Phase 5B found no evidence basis for a specific "N repeats in X seconds"
# number and Phase 5C explicitly deferred choosing one; Phase 5D's product
# decision was to accept that risk now rather than continue waiting (see
# this phase's own report for the tradeoff), NOT that these particular
# numbers have since been validated. Revisit against real production logs
# (the repetition_shadow_observation log line below) once available.
_REPETITION_SHADOW_WINDOW_SECONDS = 12.0
_REPETITION_SHADOW_MIN_MATCHES = 3
_REPETITION_SHADOW_SIMILARITY_THRESHOLD = 0.8
# Bounds the rolling history itself (independent of the window above) so a
# pathologically long call can't grow this list without bound -- deliberately
# small, since only very recent transcripts are ever relevant to a shadow
# window measured in seconds, not minutes.
_REPETITION_SHADOW_HISTORY_MAXLEN = 8


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

        # Phase 5C shadow-mode repetition observation state -- see the
        # module-level EXPERIMENTAL SHADOW-MODE THRESHOLDS comment above.
        # Bounded deque of (normalized_text, monotonic_timestamp) for the
        # most recent non-blank transcripts THIS PROCESSOR HAS SEEN SINCE
        # THE LAST BOT TURN -- never persisted, never longer than
        # _REPETITION_SHADOW_HISTORY_MAXLEN, cleared whenever a
        # BotStoppedSpeakingFrame occurs (see Step 6 of this phase's own
        # brief: a guest saying "Yes" -> bot responds -> guest saying "Yes"
        # again must NOT read as the same suspicious streak a guest's
        # background repeating "No" with no intervening bot turn would).
        self._recent_transcripts: deque[tuple[str, float]] = deque(
            maxlen=_REPETITION_SHADOW_HISTORY_MAXLEN
        )
        # Phase 5D: the repetition-shadow verdict for the MOST RECENT
        # non-blank transcript observed since the last completed turn --
        # UserStoppedSpeakingFrame carries no text of its own (it's a bare
        # marker frame), so this is how process_frame's own
        # UserStoppedSpeakingFrame branch knows whether the turn that just
        # completed looked like a repeated/background pattern. Cleared
        # after every UserStoppedSpeakingFrame consumes it and on every
        # BotStoppedSpeakingFrame (a stale verdict must never leak into a
        # later, unrelated turn) -- NOT cleared on UserStartedSpeakingFrame,
        # since a new turn's own TranscriptionFrame always arrives and
        # overwrites this before the new turn's own UserStoppedSpeakingFrame
        # could ever read it (confirmed directly: SpeechTimeoutUserTurnStop
        # Strategy's wait_for_transcript=True gate means UserStoppedSpeaking
        # Frame structurally cannot fire without a TranscriptionFrame having
        # arrived first for that same turn).
        self._pending_turn_is_repetition_candidate = False

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
        logger.info("call_end_requested")
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

        if isinstance(frame, BotStartedSpeakingFrame) and not self._ended:
            # The bot (a real reply, a slow_tool_filler filler line, or one
            # of this processor's own nudges) is now actually producing
            # audio on the line. Confirmed live: this frame was previously
            # never watched here at all, so the countdown kept running
            # while the bot was mid-answer or a slow tool call was still in
            # flight -- if generation+speaking (or a slow tool) took close
            # to or over timeout_seconds, a nudge could fire DURING the
            # bot's own answer, and in the worst case (a slow tool call
            # with no fast reply, e.g. the ~30s recommend_properties/
            # SearchApi.io paths documented in slow_tool_filler.py) enough
            # nudge cycles could elapse to hang up on a guest who was still
            # waiting for an answer they'd never gotten a chance to respond
            # to -- confirmed reachable since nothing previously reset
            # _prompts_sent except a completed GUEST turn. Cancelling the
            # timer here, symmetric with the UserStartedSpeakingFrame branch
            # below, closes both: BotStoppedSpeakingFrame (unchanged) is
            # still what restarts it once the bot actually finishes.
            logger.debug("silence_watchdog_timer_paused_bot_speaking")
            await self._cancel_timer()
        elif isinstance(frame, BotStoppedSpeakingFrame) and not self._ended:
            if self._end_requested:
                self._ended = True
                logger.info("final_audio_completed")
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
            # A real bot turn just completed (greeting, a genuine reply, or
            # a nudge) -- clears the shadow-repetition history unconditionally
            # (even on the _end_requested/hangup branch above, since a bot
            # turn genuinely completed either way; no more transcripts matter
            # once _ended is set regardless). This is what makes "guest says
            # Yes -> bot responds -> guest says Yes again" read as two
            # independent, unrelated observations rather than a two-in-a-row
            # repeated streak.
            self._recent_transcripts.clear()
            self._pending_turn_is_repetition_candidate = False
        elif isinstance(frame, UserStartedSpeakingFrame):
            # Phase 5D: the guest is ACTIVELY speaking right now (this frame
            # only exists once pipecat's own VAD-driven turn-start strategy
            # has fired -- see this module's own docstring for the wiring
            # fix that made this frame reach this processor's position at
            # all). Cancel any pending nudge timer so a nudge can never fire
            # mid-utterance and talk over the guest -- confirmed empirically
            # during this phase's investigation that this frame arrives
            # upstream at exactly this processor's pipeline position.
            # Deliberately does NOT reset _prompts_sent here: speech having
            # STARTED is not yet proof of a meaningful completed response
            # (Step 9 of this phase's brief) -- only a genuinely completed
            # turn (UserStoppedSpeakingFrame, below) counts as that.
            logger.debug("silence_watchdog_timer_paused_guest_speaking")
            await self._cancel_timer()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            # Phase 5D: THE meaningful-response boundary. This frame only
            # ever fires once pipecat's own turn-stop strategy has confirmed
            # BOTH a VAD-detected stop AND a non-empty transcript for that
            # utterance (SpeechTimeoutUserTurnStopStrategy's own
            # wait_for_transcript=True gate, confirmed directly against its
            # source) -- structurally stronger than "any non-blank
            # TranscriptionFrame", which is what this replaces as the
            # strike-counter reset trigger.
            if self._pending_turn_is_repetition_candidate:
                # The completed turn's text matched the repetition-shadow
                # signal (Phase 5C, graduated to load-bearing this phase) --
                # do NOT reset the strike counter, so a sustained repeated/
                # background pattern can still exhaust the 2-follow-up cap
                # instead of resetting it forever. The guest still gets a
                # full fresh timeout window below (restarting the timer is
                # unconditional) -- this only withholds "that counted as
                # real progress", never shortens anyone's response time.
                logger.debug("silence_watchdog_turn_completed_but_repetition_candidate_not_resetting_strikes")
            else:
                logger.debug("silence_watchdog_turn_completed_resetting_strikes")
                self._prompts_sent = 0
            if self._end_requested and self._conversation_state is not None:
                self._conversation_state.mark_reopened()
            self._end_requested = False
            self._pending_turn_is_repetition_candidate = False
            # A genuine turn just ended -- start a fresh full window for
            # whatever comes next, same "restart from here, not from call
            # start" reasoning as the BotStoppedSpeakingFrame branch above.
            await self._restart_timer()
        elif isinstance(frame, TranscriptionFrame):
            if frame.text and frame.text.strip():
                # Phase 5A observability: length only, never the transcript
                # text itself (guest content) -- enough to reconstruct, from
                # logs alone, that a transcript arrived at this point in the
                # call, without logging what was said.
                logger.debug(
                    "silence_watchdog_transcript_observed transcript_chars={}",
                    len(frame.text.strip()),
                )
                # Phase 5D: feeds the repetition-shadow signal and caches its
                # verdict for the UserStoppedSpeakingFrame handler above to
                # consult once this utterance's turn actually completes --
                # UserStoppedSpeakingFrame itself carries no text (it's a
                # bare marker frame), so this is the only point where the
                # text is available to judge.
                self._pending_turn_is_repetition_candidate = self._observe_repetition_shadow(frame.text)
                # Cancels a pending end-of-call request the moment ANY
                # transcript arrives, deliberately NOT gated on full turn
                # completion: if the guest speaks up again while/after the
                # agent's closing line is still playing (e.g. they thought
                # of one more question), that's already a clear enough
                # signal the call isn't actually over -- don't hang up on
                # top of them while waiting for turn-completion latency.
                if self._end_requested and self._conversation_state is not None:
                    self._conversation_state.mark_reopened()
                self._end_requested = False
            # Blank/whitespace-only transcripts (background noise, breathing)
            # are exactly the false-positive case that caused an unprompted
            # reply live -- swallow them here too; they're still forwarded
            # downstream unchanged below since other processors may care
            # (e.g. the turn-stop strategy's own empty-transcript handling).

        await self.push_frame(frame, direction)

    def _observe_repetition_shadow(self, text: str) -> bool:
        """Computes, logs, and returns whether this transcript participates
        in a repeated pattern -- see the module-level EXPERIMENTAL
        SHADOW-MODE THRESHOLDS comment for why the specific numbers used
        here are not validated against real call data.

        Phase 5C built this as observation-only (return value discarded).
        Phase 5D graduated it to load-bearing: the return value is now
        consulted by process_frame's UserStoppedSpeakingFrame branch to
        decide whether a completed turn counts as real engagement-progress
        for the strike counter -- see that branch's own comment.

        This does NOT claim the transcript is background/noise/invalid
        guest speech -- deliberately named/logged as "repetition_shadow_
        candidate", never "is_background"/"is_noise"/"reject_guest": this
        stack has no speaker attribution (Phase 5B), so no signal available
        here can support that stronger claim. It only observes "does this
        transcript textually resemble other very recent transcripts, with
        no bot turn in between" -- a knowingly weaker, purely structural
        property. Consulted only alongside a genuinely completed turn
        (UserStoppedSpeakingFrame already requires VAD-confirmed stop +
        non-empty transcript) -- never used to reject a turn outright or to
        suppress the guest's own speech, only to decide whether the strike
        counter treats it as fresh progress.

        Deliberately conservative about STT re-finalization (Phase 5B
        Section 7/10, re-confirmed relevant in Phase 5D): Sarvam can emit a
        corrected finalized TranscriptionFrame for the same utterance
        shortly after the first one. This method cannot distinguish that
        from genuine repeated background speech -- both look identical
        (near-identical text, no bot turn in between, close in time) -- so
        it does not try to. A false positive here (a genuine STT correction
        misread as repetition) costs the guest nothing but a strike-counter
        reset they'd have gotten anyway from the NEXT genuine turn a moment
        later -- the timer itself still restarts unconditionally either way
        (see the UserStoppedSpeakingFrame branch), so no response window is
        ever shortened by this signal being wrong.
        """
        normalized = _normalize(text)
        now = time.monotonic()

        # Drop history entries older than the shadow window -- "recent" is
        # defined relative to THIS transcript's arrival, not wall-clock
        # buckets, so a slow trickle of otherwise-unrelated transcripts
        # can't accumulate matches across an unbounded span.
        while self._recent_transcripts and now - self._recent_transcripts[0][1] > _REPETITION_SHADOW_WINDOW_SECONDS:
            self._recent_transcripts.popleft()

        prior_match_count = sum(
            1
            for prior_text, _ in self._recent_transcripts
            if _word_overlap(normalized, prior_text) >= _REPETITION_SHADOW_SIMILARITY_THRESHOLD
        )
        # +1: this transcript counts as a match with itself for the purpose
        # of "how many transcripts in the current streak", so a genuinely
        # repeated pattern of exactly _REPETITION_SHADOW_MIN_MATCHES total
        # occurrences (not _MIN_MATCHES prior ones) is what the threshold
        # below actually measures.
        total_matches = prior_match_count + 1
        repetition_shadow_candidate = total_matches >= _REPETITION_SHADOW_MIN_MATCHES

        elapsed_since_previous_ms = (
            int((now - self._recent_transcripts[-1][1]) * 1000) if self._recent_transcripts else None
        )

        # Metadata only -- normalized text is never logged (it's still
        # guest content, just lightly transformed), matching Phase 5A's
        # existing "length/counts only" discipline for this processor.
        logger.debug(
            "repetition_shadow_observation repetition_shadow_candidate={} prior_match_count={} "
            "transcript_chars={} elapsed_since_previous_transcript_ms={} history_size={}",
            repetition_shadow_candidate,
            prior_match_count,
            len(text.strip()),
            elapsed_since_previous_ms,
            len(self._recent_transcripts),
        )

        self._recent_transcripts.append((normalized, now))
        return repetition_shadow_candidate

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
            # append_to_context=True (previously False): confirmed live this
            # left every nudge/goodbye line invisible on the calls-page
            # transcript, which is built directly from context.messages
            # (app/voice/pipeline.py's on_pipeline_finished) -- a host
            # reviewing a call had no way to see that Mira ever spoke here
            # at all. Known, accepted risk: pipecat's own aggregator never
            # merges consecutive same-role messages (confirmed directly
            # against LLMContext.add_message -- a plain list.append, no
            # alternation logic), so two unanswered nudges in a row now
            # produce two consecutive "assistant" entries in context.
            # Harmless on Groq (this app's current production
            # llm_provider), but Anthropic's Messages API requires strict
            # user/assistant alternation and would reject a request built
            # from a context containing this shape -- if llm_provider is
            # ever switched to "anthropic" (config.py), this needs
            # revisiting before that switch, not after.
            await self.push_frame(TTSSpeakFrame(self._goodbye_text, append_to_context=True))
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
        # append_to_context=True -- see the goodbye TTSSpeakFrame earlier in
        # this same method for why, and the documented consecutive-
        # assistant-role / Anthropic-alternation risk this accepts.
        await self.push_frame(TTSSpeakFrame(self._prompt_texts[prompt_index], append_to_context=True))
        # Restart the clock directly rather than waiting for TTS to actually
        # speak the nudge and transport to report BotStoppedSpeakingFrame --
        # that round trip works too (this frame will still arrive and no-op
        # into a timer restart) but makes the next prompt's timing depend on
        # TTS speed, and races against pipeline teardown once EndFrame is
        # pushed. Restarting here instead makes each prompt exactly
        # timeout_seconds apart regardless of TTS latency.
        await self._restart_timer()
