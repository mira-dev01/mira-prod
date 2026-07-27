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

Two independent triggers, matching the two failure shapes actually seen live:
1. Near-duplicate sentences: each completed sentence in this response is
   compared (by word-set overlap) against every earlier sentence in the SAME
   response. A high-overlap match (worded differently but asking/saying
   the same thing) arms cutting.
2. Degenerate short-fragment flood: several very short "sentences" in a row
   (mostly punctuation, e.g. the ".. .. .." flood) also arms cutting --
   these are too short for the word-overlap check to ever catch on its own.
"""

import re

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

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


def _normalize(sentence: str) -> str:
    return re.sub(r"\s+", " ", _NON_WORD_RE.sub(" ", sentence.lower())).strip()


def _word_overlap(a: str, b: str) -> float:
    words_a, words_b = set(a.split()), set(b.split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / max(len(words_a), len(words_b))


class RepetitionGuardProcessor(FrameProcessor):
    """Cuts a response short, mid-stream, the moment it starts repeating
    itself -- every other response passes through unbuffered."""

    def __init__(self):
        super().__init__()
        self._seen_sentences: list[str] = []
        self._sentence_buffer = ""
        self._short_fragment_streak = 0
        self._cutting = False

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
            self._cutting = False
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    def _consume(self, text: str) -> None:
        self._sentence_buffer += text
        parts = _SENTENCE_SPLIT_RE.split(self._sentence_buffer)
        # The last part may be an incomplete sentence (more text still
        # streaming in) -- keep it in the buffer, only judge the completed
        # ones ahead of it.
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

        self._seen_sentences.append(normalized)
        return False
