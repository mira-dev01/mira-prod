"""Code-level backstop guaranteeing a single LLM turn can't repeat itself
into the guest's ear.

Confirmed live 2026-07-27: on a long, noisy (mixed-script, low-signal) call,
one Groq completion came back as 3072 completion tokens -- the same
clarifying question paraphrased dozens of times back to back, some turns
degenerating into a flood of near-empty ".. .. .." fragments -- all spoken to
the guest verbatim. app/voice/pipeline.py's max_completion_tokens=400 cap
bounds how much of this can ever be generated, but a cap alone doesn't
guarantee zero repetition inside that budget -- 400 tokens is still enough
room for several repeats of a short question. This processor is the
deterministic guarantee: it can't stop the model from wanting to repeat
itself, but it can stop a second (or later) repeat from ever reaching TTS.

Design: stream every LLMTextFrame straight through immediately by default --
no buffering, no added latency to the normal, non-repeating case (this is
the same latency trade-off reasoning already applied to reasoning_effort;
see pipeline.py's comment on that). Only once repetition is actually
detected mid-stream does it start dropping frames, silently, for the rest of
that one response -- the reply just ends a little early instead of spiraling
into duplicated or degenerate text. Because the frame that completes a
sentence has already been forwarded by the time its content is checked for a
duplicate, the guest may hear one repeat before the guard reacts -- an
acceptable, explicitly bounded cost in exchange for zero overhead on every
normal turn.

Three independent triggers, matching the failure shapes actually seen live:
1. Near-duplicate sentences: each completed sentence in this response is
   compared (by word-set overlap) against every earlier sentence in the SAME
   response. A high-overlap match (worded differently but asking/saying
   the same thing) arms cutting.
2. Degenerate short-fragment flood: several very short "sentences" in a row
   (mostly punctuation, e.g. the ".. .. .." flood) also arms cutting --
   these are too short for the word-overlap check to ever catch on its own.
3. Phase 4.2 (documentation/agent-conversation-improvement.md): a structured
   fact repeat ACROSS turns, not just within one response -- trigger 1 above
   only ever compares sentences within the SAME response (by design, see
   test_cut_state_resets_between_responses), so it structurally cannot catch
   "As I mentioned, the villa in Goa is available" echoing an EARLIER turn's
   "The Ocean View villa is open for those dates" (low word overlap, same
   fact, different turns). When a ConversationState is supplied, this
   processor also checks whether a completed sentence both (a) names a
   property already in state.recommendations_shown/quoted_price and (b)
   restates a price/availability-shaped claim about it, without the guest
   having asked again or something new being known -- catching the
   same-content-different-wording repeat the text-similarity check alone
   would miss, without banning legitimately mentioning that property's name
   again for an unrelated reason (e.g. confirming a booking for it).
"""

import re

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.voice.conversation_state import ConversationState

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_NON_WORD_RE = re.compile(r"[^\w\s]")

# Below this many words, word-overlap similarity is too noisy to trust (e.g.
# "Got it." vs "Sure thing." would false-positive) -- these are covered by
# the short-fragment-flood check instead, not the near-duplicate check.
_MIN_WORDS_FOR_SIMILARITY_CHECK = 4
_SIMILARITY_THRESHOLD = 0.6
# A "short fragment" is a completed sentence with fewer normalized characters
# than this -- the degenerate ".. .. .." flood is entirely made of these.
_SHORT_FRAGMENT_MAX_CHARS = 2
_SHORT_FRAGMENT_FLOOD_THRESHOLD = 4


_PRICE_RE = re.compile(r"₹\s?([\d,]+)")


def _normalize(sentence: str) -> str:
    return re.sub(r"\s+", " ", _NON_WORD_RE.sub(" ", sentence.lower())).strip()


def _word_overlap(a: str, b: str) -> float:
    words_a, words_b = set(a.split()), set(b.split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / max(len(words_a), len(words_b))


def _prices_in(text: str) -> set[str]:
    """Extracts every ₹-prefixed number as a normalized digit string (commas
    stripped) so "₹18,700" and "18700" compare equal regardless of comma
    formatting. Used only for the exact-number-repeat check (4.2 below) --
    a DIFFERENT number (e.g. a discount re-quote) is real new information
    and must never be treated as a repeat."""
    return {m.replace(",", "") for m in _PRICE_RE.findall(text)}


class RepetitionGuardProcessor(FrameProcessor):
    """Cuts a response short, mid-stream, the moment it starts repeating
    itself -- every other response passes through unbuffered."""

    def __init__(self, conversation_state: ConversationState | None = None):
        super().__init__()
        self._seen_sentences: list[str] = []
        self._sentence_buffer = ""
        self._short_fragment_streak = 0
        self._cutting = False
        # Phase 4.2: optional so every existing call site/test that
        # constructs this processor without a ConversationState keeps
        # working unchanged (the structured cross-turn check below is
        # simply skipped, same as every other Phase 3/4 processor's
        # optional-state pattern).
        self._state = conversation_state
        # Cross-RESPONSE memory (deliberately NOT reset in
        # LLMFullResponseStartFrame, unlike _seen_sentences) -- tracks which
        # already-known structured facts (property name + its exact quoted
        # price/recommended-price figure) have already been SPOKEN at least
        # once this call. The first time a known fact is said, it must be
        # allowed through (that's the tool result actually being spoken,
        # required by GOLDEN_RULES) -- only a SECOND+ utterance of the exact
        # same fact, unprompted, is a repeat.
        self._spoken_facts: set[str] = set()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._seen_sentences = []
            self._sentence_buffer = ""
            self._short_fragment_streak = 0
            self._cutting = False
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMTextFrame):
            if self._cutting:
                # Silently dropped -- the guest just doesn't hear the rest of
                # this response, rather than hearing it repeat.
                return
            await self.push_frame(frame, direction)
            self._consume(frame.text)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            # Phase 4.2 fix (documentation/agent-conversation-improvement.md):
            # a response whose final sentence has no trailing text after it
            # (the common case -- a reply that just ends) left that last
            # sentence sitting in _sentence_buffer forever, un-judged AND
            # un-recorded -- _consume only ever judges parts[:-1], treating
            # the final segment as "possibly incomplete, more may still
            # stream in". This was a real, pre-existing gap (not introduced
            # by Phase 4.2) that Phase 4.2's own structured cross-turn check
            # exposed directly: the common shape of "a price quote as the
            # final sentence of a reply" was silently never being recorded
            # into _seen_sentences/_spoken_facts. Note this can only ever
            # RECORD the final sentence for future comparison, never
            # retroactively withhold its own frame -- that frame was already
            # pushed downstream by the LLMTextFrame branch above, before
            # this judgment runs (same streaming trade-off already documented
            # for every other sentence in this module's docstring). Skipped
            # entirely if already cutting -- that buffered text was never
            # actually spoken, so it must not be recorded as if it were.
            if self._sentence_buffer.strip() and not self._cutting:
                self._judge_sentence(self._sentence_buffer)
            self._sentence_buffer = ""
            self._cutting = False
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    def _consume(self, text: str) -> None:
        self._sentence_buffer += text
        parts = _SENTENCE_SPLIT_RE.split(self._sentence_buffer)
        # The last part may be an incomplete sentence (more text still
        # streaming in) -- keep it in the buffer, only judge the completed
        # ones ahead of it. (LLMFullResponseEndFrame above is what flushes
        # this final segment once the response is confirmed done.)
        self._sentence_buffer = parts[-1]
        for sentence in parts[:-1]:
            if self._judge_sentence(sentence):
                self._cutting = True
                return

    def _judge_sentence(self, raw_sentence: str) -> bool:
        """Returns True if this sentence proves the response is repeating
        itself and everything after it should be cut."""
        normalized = _normalize(raw_sentence)

        if len(normalized) <= _SHORT_FRAGMENT_MAX_CHARS:
            self._short_fragment_streak += 1
            return self._short_fragment_streak >= _SHORT_FRAGMENT_FLOOD_THRESHOLD
        self._short_fragment_streak = 0

        if len(normalized.split()) >= _MIN_WORDS_FOR_SIMILARITY_CHECK:
            for seen in self._seen_sentences:
                if _word_overlap(normalized, seen) >= _SIMILARITY_THRESHOLD:
                    return True

        if self._is_unprompted_structured_repeat(raw_sentence):
            return True

        self._seen_sentences.append(normalized)
        return False

    def _is_unprompted_structured_repeat(self, raw_sentence: str) -> bool:
        """Phase 4.2: catches a same-fact-different-wording repeat ACROSS
        turns that the within-response word-overlap check structurally
        cannot see. Narrowly scoped to avoid ever blocking a legitimate
        second mention of a property's name for an unrelated reason (e.g.
        first recommending it, later confirming a booking for it) -- only
        fires when the sentence names an already-known property AND
        restates the EXACT already-quoted price figure for it (not just the
        name alone), since restating the identical number, unprompted, is a
        much stronger and narrower repeat signal than a bare name match."""
        if self._state is None or not self._state.quoted_price:
            return False

        qp = self._state.quoted_price
        property_name = qp.get("property_name", "")
        if not property_name or property_name.lower() not in raw_sentence.lower():
            return False

        sentence_prices = _prices_in(raw_sentence)
        quoted_total_digits = f"{qp['total']:,.0f}".replace(",", "")
        if quoted_total_digits not in sentence_prices:
            return False

        fact_key = f"{property_name.lower()}|{quoted_total_digits}"
        if fact_key in self._spoken_facts:
            return True
        self._spoken_facts.add(fact_key)
        return False
