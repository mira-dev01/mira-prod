"""Shared, deterministic text-language heuristics -- no NLP, no embeddings,
no LLM call. Single source of truth for both directions that need this:
app/voice/conversation_style.py (analyzing the GUEST's transcribed speech to
drive the Style Engine) and app/voice/response_compliance.py (validating the
LLM's own generated reply against the expected style). Extracted from
response_compliance.py's original implementation with zero behavior change --
both call sites now share one set of thresholds/token lists instead of two
copies silently drifting apart.
"""

from __future__ import annotations

import unicodedata

# Common romanized Hindi/Hinglish function words -- the actual signal that
# distinguishes "genuine Hinglish text, just happens to render in Latin
# script" from "plainly English". Deliberately small and closed-class
# (pronouns/particles/common verbs a Hinglish sentence is very likely to
# contain at least one of), not an attempt at general language ID -- a
# lightweight heuristic, not NLP. Excludes any token that collides with a
# common English word ("the", "is", "to", "ka" as in "-ka" homonyms etc.
# were considered and rejected) -- false-positiving on plain English text
# would defeat the point.
HINGLISH_TOKENS = frozenset(
    {
        "hai", "hain", "hoon", "hun",
        "aapka", "aapke", "aapki", "mujhe", "humein", "hume", "unhe",
        "mein", "kaise", "kyun", "kyunki", "kahan", "kitna", "kitne",
        "nahi", "nahin", "haan", "bhi", "toh", "achha", "accha", "theek", "thik",
        "shukriya", "chahiye", "milega", "milegi", "karenge", "karna", "karni", "karo",
        "bhai", "yaar", "abhi", "phir", "iske", "uske", "jaise", "waise",
    }
)

MIN_CHARS_FOR_CONFIDENT_VERDICT = 12


def devanagari_ratio(text: str) -> tuple[float, int]:
    """O(n) single pass. Returns (devanagari_fraction_of_letters, letter_count)
    -- ratio is computed over alphabetic codepoints only (digits/punctuation/
    whitespace excluded), so a price or a property name doesn't skew it."""
    devanagari = 0
    letters = 0
    for ch in text:
        if not ch.isalpha():
            continue
        letters += 1
        if "DEVANAGARI" in unicodedata.name(ch, ""):
            devanagari += 1
    if letters == 0:
        return 0.0, 0
    return devanagari / letters, letters


def has_hinglish_token(text: str) -> bool:
    # Strip trailing punctuation per word so "hai," / "kya?" still match --
    # cheap O(n) tokenization, no regex backtracking risk.
    for word in text.lower().split():
        stripped = word.strip(".,!?;:\"'()")
        if stripped in HINGLISH_TOKENS:
            return True
    return False


def english_word_ratio(text: str) -> float:
    """O(n). Fraction of whitespace-split tokens that are pure ASCII-letter
    words -- a cheap proxy for "how much of this turn was spoken in English"
    used by the Style Engine's weighted scoring. Not letter-level (unlike
    devanagari_ratio) since a romanized Hindi word ("kaise") and an English
    word are both pure-ASCII -- token-level is the right granularity for
    telling "aapka check-in" (1 English token of 2) from "check the
    calendar" (4 of 4), which letter-ratio alone can't distinguish."""
    words = [w.strip(".,!?;:\"'()") for w in text.split()]
    words = [w for w in words if w]
    if not words:
        return 0.0
    # isalpha() alone rejects a genuine English contraction ("let's",
    # "don't") since the internal apostrophe isn't alphabetic -- strip
    # internal apostrophes before the alpha check so a contraction still
    # counts as one English token, not silently excluded from the ratio.
    english = sum(
        1 for w in words if w.isascii() and w.replace("'", "").isalpha() and w.lower() not in HINGLISH_TOKENS
    )
    return english / len(words)
