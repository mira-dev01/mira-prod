from app.voice.turn_strategies import HybridCompletenessUserTurnStopStrategy, _is_incomplete


def test_is_incomplete_trailing_english_conjunction():
    assert _is_incomplete("I need it for two nights and") is True


def test_is_incomplete_trailing_hindi_conjunction_devanagari():
    # Sarvam STT (mode="codemix") transcribes Hindi in Devanagari script --
    # the heuristic must recognize both scripts, not just romanized Hinglish.
    assert _is_incomplete("मुझे चाहिए और") is True


def test_is_incomplete_trailing_hinglish_conjunction_romanized():
    assert _is_incomplete("mujhe chahiye aur") is True


def test_is_incomplete_trailing_comma():
    assert _is_incomplete("Two nights, three guests,") is True


def test_is_incomplete_very_short_with_no_terminal_punctuation():
    assert _is_incomplete("yes") is True


def test_is_incomplete_empty_string():
    assert _is_incomplete("") is True
    assert _is_incomplete("   ") is True


def test_is_complete_full_sentence_with_terminal_punctuation():
    assert _is_incomplete("Two nights please.") is False


def test_short_answer_with_only_a_period_is_still_incomplete():
    # A lone "." is Sarvam's default sentence-final mark and is added on almost any
    # falling-pitch pause, mid-thought included ("...my phone number is 9." was read
    # as complete on a single digit, confirmed live), so it must not rescue a short
    # utterance from the length check. See _is_incomplete's own comment.
    assert _is_incomplete("Yes.") is True


def test_short_answer_with_confident_punctuation_is_complete():
    # "?" / "!" are deliberate completeness signals STT only adds when confident.
    assert _is_incomplete("Yes!") is False
    assert _is_incomplete("Kasauli?") is False


def test_strategy_instantiates_with_defaults():
    strategy = HybridCompletenessUserTurnStopStrategy()
    assert isinstance(strategy, HybridCompletenessUserTurnStopStrategy)


def test_strategy_instantiates_with_custom_timeouts():
    strategy = HybridCompletenessUserTurnStopStrategy(base_timeout=1.0, extension_timeout=0.5, max_wait=3.0)
    assert strategy._base_timeout == 1.0
    assert strategy._extension_timeout == 0.5
    assert strategy._max_wait == 3.0
