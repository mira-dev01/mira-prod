"""Covers app/voice/language_heuristics.py -- shared, deterministic text-
language detection used by both the Style Engine (app/voice/conversation_style.py,
analyzing guest speech) and the Response Validator
(app/voice/response_compliance.py, validating LLM replies). Extracted from
response_compliance.py's original implementation with zero behavior change --
these tests moved with the code, not duplicated.
"""

from app.voice.language_heuristics import devanagari_ratio, english_word_ratio, has_hinglish_token


def test_devanagari_ratio_pure_devanagari():
    ratio, letters = devanagari_ratio("आपका स्वागत है")
    assert ratio == 1.0
    assert letters > 0


def test_devanagari_ratio_pure_latin():
    ratio, letters = devanagari_ratio("Welcome to our property")
    assert ratio == 0.0
    assert letters > 0


def test_devanagari_ratio_ignores_digits_and_punctuation():
    ratio, letters = devanagari_ratio("₹12,000 / night!!")
    assert letters == "night".__len__()  # only "night"'s letters count
    assert ratio == 0.0


def test_devanagari_ratio_empty_text_no_zero_division():
    ratio, letters = devanagari_ratio("123 -- ...")
    assert ratio == 0.0
    assert letters == 0


def test_has_hinglish_token_detects_common_words():
    assert has_hinglish_token("Aapka check-in kal hai") is True


def test_has_hinglish_token_does_not_false_positive_on_the():
    """'the' must never match -- it's the single most common English word;
    an earlier draft of this token set included 'the' intending the Hindi
    past-tense particle and it silently broke every plain-English check."""
    assert has_hinglish_token("Let me check the calendar for you") is False


def test_has_hinglish_token_no_match_on_plain_english():
    assert has_hinglish_token("Sure, let me check that for you right away.") is False


def test_english_word_ratio_pure_english():
    assert english_word_ratio("Sure, let me check that for you right away.") == 1.0


def test_english_word_ratio_counts_contractions_as_english():
    """A bare isalpha() check rejects "let's"/"don't" since the internal
    apostrophe isn't alphabetic, silently undercounting genuine English
    content -- found while testing the Style Engine's hysteresis (a run of
    "Actually let's continue in English please" repeats never crossed the
    English threshold because "let's" was dropped from the numerator every
    time)."""
    assert english_word_ratio("Let's continue in English please") == 1.0


def test_english_word_ratio_romanized_hindi_with_english_loanword():
    # "haan"/"mujhe"/"karni" are recognized Hinglish tokens (excluded);
    # "booking" is a genuine English loanword and "thi" is a short romanized
    # Hindi word NOT in the closed-class list (deliberately kept out --
    # too short/ambiguous a token to safely exclude without risking
    # collisions elsewhere) -- 2 of 5 tokens read as English, ratio 0.4.
    # Documents the heuristic's real, imperfect behavior on a genuinely
    # short closed-class list rather than assuming a stricter classifier
    # that doesn't exist -- this residual noise is exactly why the Style
    # Engine uses multi-turn weighted scoring with hysteresis rather than
    # trusting any single turn's ratio.
    ratio = english_word_ratio("haan mujhe booking karni thi")
    assert ratio == 0.4


def test_english_word_ratio_mixed():
    # "hello" -> English, "bhai" -> recognized Hinglish token (excluded).
    ratio = english_word_ratio("hello bhai")
    assert ratio == 0.5


def test_english_word_ratio_excludes_recognized_hinglish_tokens():
    ratio = english_word_ratio("check the mein hai")
    # "check" -> English, "the" -> English (not a Hinglish token), "mein"/"hai" -> Hinglish
    assert ratio == 0.5


def test_english_word_ratio_empty_text():
    assert english_word_ratio("") == 0.0


def test_english_word_ratio_ignores_devanagari_tokens():
    ratio = english_word_ratio("मुझे booking करनी है")
    # Devanagari tokens are neither ASCII nor alpha-by-isascii, so only
    # "booking" counts as an English token out of the whitespace-split set.
    assert 0.0 < ratio < 1.0
