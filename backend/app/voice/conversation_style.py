"""The Conversation Style Engine -- computes a single, immutable
ConversationStyle from a rolling window of the guest's actual turns, with
hysteresis so one noisy turn can't flip the committed language/tone the LLM
is told to speak in.

Why this exists: today's language mechanism (app/voice/language_sync.py +
ConversationState.current_spoken_language) is a single enum, unconditionally
OVERWRITTEN by whatever Sarvam tagged the most recent utterance -- there is
no memory, no ratio, no confidence, and nothing preventing one ambiguous or
mis-tagged turn from flipping the LLM's instruction turn to turn. Confirmed
live: a guest's "nahi but mujhe sure nahi" (mostly Hindi/Hinglish) can read
as English-leaning under a naive single-turn heuristic (see
test_language_heuristics.py's documented false-positive case), which is
exactly the kind of single-turn noise this module is built to absorb rather
than react to.

This module owns DETECTION + SCORING + HYSTERESIS only. It is a pure
function of (turn text, prior state) -> ConversationStyle -- no I/O, no LLM
call, no randomness, fully unit-testable in isolation. It does not replace
LanguageSyncProcessor (which still drives the live TTS-language switch off
Sarvam's own per-utterance tag -- a different, lower-latency, audio-facing
concern) or ConversationState's existing current_spoken_language/
explicit_language_preference fields (which the Response Validator, and any
other caller, still read directly for their own purposes). It ADDS a
higher-level, slower-moving "what style should the LLM commit to right now"
judgment on top of the same underlying signal.

Reuses app/voice/language_heuristics.py (devanagari_ratio, has_hinglish_token,
english_word_ratio) -- the exact same deterministic text checks the Response
Validator already uses to check the LLM's OWN output, applied here to the
GUEST's transcribed input instead. One shared heuristic module, two
consumers, not two independent implementations that could silently drift
apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.voice.conversation_state import ConversationState
from app.voice.language_heuristics import devanagari_ratio, english_word_ratio, has_hinglish_token

Language3 = Literal["ENGLISH", "HINDI", "HINGLISH"]
Script = Literal["DEVANAGARI", "ROMAN"]
Politeness = Literal["LOW", "MEDIUM", "HIGH"]
Tone = Literal["CASUAL", "PREMIUM", "LUXURY"]
HospitalityStyle = Literal["AIRBNB_HOST"]

# How many recent turns the weighted score considers. Deliberately small --
# this is meant to track "how is the guest talking RIGHT NOW", not the
# whole call's history (an early English "hello" shouldn't still be
# influencing style 15 turns into a Hindi conversation).
DEFAULT_ROLLING_WINDOW = 6

# A language-family switch (HINDI/HINGLISH <-> ENGLISH) only commits once
# the weighted score has sat on the other side of this margin around 0.5 for
# MIN_TURNS_TO_SWITCH consecutive turns. Both numbers are deliberately
# conservative -- per the spec, "one English sentence should NOT switch to
# English" -- and are named constants, not magic numbers, so they can be
# tuned from real call data later without touching the algorithm itself.
_SWITCH_MARGIN = 0.15
MIN_TURNS_TO_SWITCH = 2

# Politeness/tone are not yet computed from any real signal -- no existing
# component in this codebase produces sentiment/formality data to derive
# them from (same "don't invent a new inference source" discipline
# documentation/memory-architecture-plan.md's section 1.1 already applied to
# GuestProfile.sentiment). Fixed to sensible defaults for MIRA's actual
# product (an Airbnb host concierge) until a real signal exists -- see
# ConversationAnalyzer.analyze_turn's docstring for exactly what would need
# to change to make these dynamic.
_DEFAULT_POLITENESS: Politeness = "MEDIUM"
_DEFAULT_TONE: Tone = "PREMIUM"
_DEFAULT_HOSPITALITY_STYLE: HospitalityStyle = "AIRBNB_HOST"


@dataclass(frozen=True)
class TurnSignal:
    """One turn's raw, cheap-to-compute language signal -- the Style
    Engine's only input besides prior state. Never mutated after
    construction; StyleEngine holds a bounded list of these internally
    (its own _history), oldest dropped first."""

    english_ratio: float
    devanagari_ratio: float
    has_hinglish_token: bool


@dataclass(frozen=True)
class ConversationStyle:
    """A single immutable snapshot of how Mira should currently speak.
    Constructed only by StyleEngine.update -- never partially mutated in
    place, so a reference handed to the prompt builder or the response
    validator can't be changed out from under either by the other."""

    language: Language3
    script: Script
    english_ratio: float
    politeness: Politeness
    tone: Tone
    hospitality_style: HospitalityStyle

    # Hysteresis bookkeeping -- exposed on the object itself (not hidden
    # inside the engine) so both the prompt builder and tests can inspect
    # *why* the current style is what it is, not just what it is.
    confidence: float
    previous_style: "ConversationStyle | None"
    last_language_switch_turn: int | None
    turn_index: int


def _default_style() -> ConversationStyle:
    return ConversationStyle(
        language="ENGLISH",
        script="ROMAN",
        english_ratio=1.0,
        politeness=_DEFAULT_POLITENESS,
        tone=_DEFAULT_TONE,
        hospitality_style=_DEFAULT_HOSPITALITY_STYLE,
        confidence=0.0,
        previous_style=None,
        last_language_switch_turn=None,
        turn_index=0,
    )


class ConversationAnalyzer:
    """Turns raw guest transcript text into a TurnSignal. Deterministic,
    O(n) over the transcript, no network/DB/LLM call -- reuses
    language_heuristics.py exactly as response_compliance.py's
    LanguageComplianceRule already does for LLM output, applied here to
    guest input instead."""

    @staticmethod
    def analyze_turn(transcript_text: str) -> TurnSignal:
        d_ratio, _letters = devanagari_ratio(transcript_text)
        return TurnSignal(
            english_ratio=english_word_ratio(transcript_text),
            devanagari_ratio=d_ratio,
            has_hinglish_token=has_hinglish_token(transcript_text),
        )


# Recency weights, most-recent-first, index 0 = current turn. Linear decay
# (not exponential) -- simple, explainable, and easy to reason about/tune;
# nothing in the spec calls for a specific decay shape, and a linear ramp is
# the smallest thing that satisfies "recent turns matter more than older
# ones" without inventing an unjustified curve. Extended with 1s (equal
# weight) if rolling_window exceeds this list's length.
_RECENCY_WEIGHTS = [6, 5, 4, 3, 2, 1]


def _weight_for_index(i: int) -> int:
    if i < len(_RECENCY_WEIGHTS):
        return _RECENCY_WEIGHTS[i]
    return 1


def _weighted_english_ratio(history: list[TurnSignal]) -> float:
    """history[0] is the OLDEST turn, history[-1] is the CURRENT turn (same
    append-only order StyleEngine's own _history is built in) -- so recency
    weighting walks the list in reverse."""
    if not history:
        return 1.0
    total_weight = 0.0
    weighted_sum = 0.0
    for i, signal in enumerate(reversed(history)):
        w = _weight_for_index(i)
        weighted_sum += w * signal.english_ratio
        total_weight += w
    return weighted_sum / total_weight if total_weight else 1.0


def _weighted_devanagari_ratio(history: list[TurnSignal]) -> float:
    if not history:
        return 0.0
    total_weight = 0.0
    weighted_sum = 0.0
    for i, signal in enumerate(reversed(history)):
        w = _weight_for_index(i)
        weighted_sum += w * signal.devanagari_ratio
        total_weight += w
    return weighted_sum / total_weight if total_weight else 0.0


# Band edges for the 3-way split. ENGLISH requires a HIGH ratio (mostly-
# English tokens); HINDI requires a LOW one; HINGLISH is everything between
# -- a real, meaningfully-mixed middle band, not a fallback catch-all.
_ENGLISH_BAND_MIN = 0.85
_HINDI_BAND_MAX = 0.35


def _language_family_from_score(weighted_english: float, has_recent_hinglish_token: bool) -> Language3:
    """Deterministic mapping from the weighted score to a 3-way language
    family. HINGLISH sits in the middle band -- meaningfully mixed, not
    purely one or the other -- and is also selected whenever a recognized
    Hinglish token was seen recently even if the ratio alone would read as
    mostly-English (the same "one signal can't be discarded just because
    another looks the opposite way" principle LanguageComplianceRule already
    applies when checking LLM output)."""
    if weighted_english >= _ENGLISH_BAND_MIN and not has_recent_hinglish_token:
        return "ENGLISH"
    if weighted_english <= _HINDI_BAND_MAX:
        return "HINDI"
    return "HINGLISH"


def _score_clearly_supports(weighted_english: float, candidate: Language3) -> bool:
    """Whether the weighted score sits solidly inside the candidate
    family's own band, not just barely across the boundary from whatever
    the current committed style is -- the fix for a subtle bug found during
    testing: comparing distance-from-0.5 (the ENGLISH/HINDI boundary) can
    never be satisfied by a HINGLISH candidate, since HINGLISH's whole
    definition is "close to the middle." Each family is checked against
    its own band edge, with a small margin past the edge for ENGLISH/HINDI
    (a stronger bar, since those are the more consequential switches away
    from the safer default of continuing to mirror mixed speech), and
    simply "inside the band" for HINGLISH."""
    if candidate == "ENGLISH":
        return weighted_english >= _ENGLISH_BAND_MIN - _SWITCH_MARGIN
    if candidate == "HINDI":
        return weighted_english <= _HINDI_BAND_MAX + _SWITCH_MARGIN
    return _HINDI_BAND_MAX < weighted_english < _ENGLISH_BAND_MIN


class StyleEngine:
    """Owns its own rolling-window history internally (_history) -- this is
    the Style Engine's own working memory, not a conversation fact, so it
    does NOT live on ConversationState (architecture boundary: ConversationState
    holds conversation facts only; ConversationStyle, including this engine,
    is the single source of truth for HOW Mira speaks, entirely self-contained).
    One StyleEngine instance lives for the life of one call (constructed once
    by ConversationStyleProcessor below), so "per-call" scoping is preserved
    exactly as before -- only the history's storage location moved, not its
    lifetime or algorithm. update() itself remains a pure function of its
    explicit arguments plus this instance's own history -- no hidden global
    state, no cross-call leakage (a fresh StyleEngine is constructed per call,
    same as ConversationState itself)."""

    def __init__(self, rolling_window: int = DEFAULT_ROLLING_WINDOW):
        self._rolling_window = rolling_window
        self._history: list[TurnSignal] = []

    def update(
        self,
        latest_signal: TurnSignal,
        previous_style: ConversationStyle | None,
        turn_index: int,
    ) -> ConversationStyle:
        """Returns the new ConversationStyle snapshot. Internally advances
        this engine's own history -- callers no longer thread a history list
        through by hand (see module docstring for why that responsibility
        moved here)."""
        new_history = [*self._history, latest_signal][-self._rolling_window :]
        self._history = new_history

        weighted_english = _weighted_english_ratio(new_history)
        weighted_devanagari = _weighted_devanagari_ratio(new_history)
        recent_hinglish_token = any(s.has_hinglish_token for s in new_history[-2:])

        candidate_language = _language_family_from_score(weighted_english, recent_hinglish_token)

        current_language = previous_style.language if previous_style is not None else None
        last_switch_turn = previous_style.last_language_switch_turn if previous_style is not None else None

        if current_language is None:
            # First real style computed for this call -- commit immediately,
            # nothing to hold hysteresis against yet. Provisional confidence
            # (not 1.0) since it's earned over multiple agreeing turns, not
            # granted on the first observation.
            committed_language = candidate_language
            last_switch_turn = turn_index
            confidence = 0.6
        elif candidate_language == current_language:
            committed_language = current_language
            confidence = min(1.0, (previous_style.confidence if previous_style else 0.5) + 0.15)
        else:
            # Candidate disagrees with the currently committed language --
            # this is exactly the hysteresis gate. Only commit the switch if
            # the score clearly supports the candidate's OWN band AND has
            # looked this way for MIN_TURNS_TO_SWITCH consecutive recent
            # turns, not just this one turn.
            score_clearly_supports_candidate = _score_clearly_supports(weighted_english, candidate_language)
            consecutive_agreement = _consecutive_turns_agreeing_with(new_history, candidate_language)

            if score_clearly_supports_candidate and consecutive_agreement >= MIN_TURNS_TO_SWITCH:
                committed_language = candidate_language
                last_switch_turn = turn_index
                confidence = 0.6
            else:
                # Hold the current style -- exactly the spec's worked
                # example: "haan but dates confirm nahi" stays HINDI, not
                # English, on the strength of one turn's noisy signal.
                committed_language = current_language
                confidence = max(0.3, (previous_style.confidence if previous_style else 0.5) - 0.1)

        script: Script = "DEVANAGARI" if weighted_devanagari > 0.3 else "ROMAN"

        new_style = ConversationStyle(
            language=committed_language,
            script=script,
            english_ratio=round(weighted_english, 3),
            politeness=_DEFAULT_POLITENESS,
            tone=_DEFAULT_TONE,
            hospitality_style=_DEFAULT_HOSPITALITY_STYLE,
            confidence=round(confidence, 3),
            previous_style=previous_style,
            last_language_switch_turn=last_switch_turn,
            turn_index=turn_index,
        )
        return new_style


def _consecutive_turns_agreeing_with(history: list[TurnSignal], language: Language3) -> int:
    """Counts back from the most recent turn how many in a row would
    independently classify (via the same per-turn threshold logic, applied
    to that single turn's own ratio rather than the weighted score) as the
    candidate language family -- the "confidence over multiple turns" the
    spec asks for, computed from the same history list already being
    maintained, no separate counter to keep in sync."""
    count = 0
    for signal in reversed(history):
        single_turn_family = _language_family_from_score(signal.english_ratio, signal.has_hinglish_token)
        if single_turn_family == language or (
            language == "HINGLISH" and single_turn_family in ("HINDI", "HINGLISH")
        ):
            count += 1
        else:
            break
    return count


def render_style_block(style: ConversationStyle, emphasized: bool = False) -> str:
    """Exactly the structured prompt block the spec asks for -- the ONLY
    place ConversationStyle is turned into prompt text. Consumed by
    state_prompt_sync.py's dynamic per-turn message; the static system
    prompt (GOLDEN_RULES) no longer carries the passive-mirroring/Hinglish-
    tone paragraphs this replaces (see system_prompt.py).

    emphasized=True renders one additional, more forceful line -- the ONLY
    mechanism by which a quality signal (ConversationQuality.
    pending_style_correction, set by StyleComplianceMonitor after a
    confirmed language mismatch) can influence the prompt: it asks THIS
    function for a stronger rendering of the exact same style, never a
    different or validator-authored instruction. state_prompt_sync.py is
    the only caller that ever passes emphasized=True."""
    language_label = {"ENGLISH": "English", "HINDI": "Hindi", "HINGLISH": "Urban Hinglish"}[style.language]
    script_label = "Devanagari" if style.script == "DEVANAGARI" else "Roman"
    tone_label = {"CASUAL": "Casual", "PREMIUM": "Premium Airbnb Host", "LUXURY": "Luxury Concierge"}[style.tone]
    english_pct = round(style.english_ratio * 100)

    block = (
        "Conversation Style\n"
        f"Language: {language_label}\n"
        f"English Ratio: {english_pct}%\n"
        f"Script: {script_label}\n"
        f"Tone: {tone_label}\n"
        "Follow this naturally. Do not explain. Do not translate. Mirror the guest naturally. "
        "Never abruptly change language."
    )
    if emphasized:
        block += (
            f"\nYour last reply did not match this -- make sure this one is clearly in {language_label}, "
            "not just close to it."
        )
    return block


class ConversationStyleProcessor(FrameProcessor):
    """Wires ConversationAnalyzer + StyleEngine into the live pipeline. Sits
    right after `stt`, alongside (not replacing) LanguageSyncProcessor --
    both read the same TranscriptionFrame independently. LanguageSyncProcessor
    keeps driving the live TTS-language switch and
    ConversationState.current_spoken_language exactly as before, untouched
    by this addition; this processor ADDS the separate, hysteresis-smoothed
    ConversationState.conversation_style on top, read only by
    StatePromptSyncProcessor's prompt-building. Never touches TTS, never
    pushes a different frame type, never blocks -- the analysis is a pure,
    O(n)-over-one-utterance function with no I/O, so this adds negligible
    per-turn latency to a pipeline stage that already does a comparable
    amount of work for the exact same frame."""

    def __init__(self, conversation_state: ConversationState | None = None, rolling_window: int = DEFAULT_ROLLING_WINDOW):
        super().__init__()
        self._conversation_state = conversation_state
        self._engine = StyleEngine(rolling_window=rolling_window)
        self._turn_index = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and self._conversation_state is not None and frame.text:
            self._turn_index += 1
            signal = ConversationAnalyzer.analyze_turn(frame.text)
            new_style = self._engine.update(
                signal,
                self._conversation_state.conversation_style,
                turn_index=self._turn_index,
            )
            self._conversation_state.conversation_style = new_style
            # Also the shared per-call turn counter for ConversationState's
            # own attention/salience tracking (touch_attention/
            # attention_score, see conversation_state.py) -- reuses this
            # exact "one real guest TranscriptionFrame = one turn" signal
            # rather than introducing a second, differently-defined turn
            # counter. Deliberately advanced here (this processor already
            # fires unconditionally on every such frame and already writes
            # to conversation_state) rather than adding a new dedicated
            # pipeline stage just to increment one counter.
            self._conversation_state.advance_turn()

        await self.push_frame(frame, direction)
