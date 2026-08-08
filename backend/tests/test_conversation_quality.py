"""Covers app/voice/conversation_quality.py -- the generic, analytics-only
home for validator output. Confirms the architecture boundary itself: this
object never influences prompt construction except through the one narrow,
documented pending_style_correction bridge.
"""

from app.voice.conversation_quality import ConversationQuality, ValidationResult


def test_validation_result_is_failure_property():
    fail = ValidationResult(rule="style_compliance", severity="FAIL", confidence=0.9)
    info = ValidationResult(rule="style_compliance", severity="INFO", confidence=1.0)
    warning = ValidationResult(rule="style_compliance", severity="WARNING", confidence=0.3)
    assert fail.is_failure is True
    assert info.is_failure is False
    assert warning.is_failure is False


def test_validation_result_is_frozen():
    import pytest

    result = ValidationResult(rule="x", severity="INFO", confidence=1.0)
    with pytest.raises(Exception):
        result.severity = "FAIL"  # type: ignore[misc]


def test_validation_result_default_metadata_is_empty_dict():
    result = ValidationResult(rule="x", severity="INFO", confidence=1.0)
    assert result.metadata == {}


def test_two_validation_results_have_independent_metadata_dicts():
    """Regression guard for the classic mutable-default-shared-across-instances
    hazard -- field(default_factory=dict) must actually give each instance
    its own dict, not one shared dict."""
    a = ValidationResult(rule="x", severity="INFO", confidence=1.0)
    b = ValidationResult(rule="y", severity="INFO", confidence=1.0)
    a.metadata["k"] = "v"
    assert b.metadata == {}


def test_record_appends_to_validations():
    quality = ConversationQuality()
    result = ValidationResult(rule="shape_compliance", severity="WARNING", confidence=1.0)
    quality.record(result)
    assert quality.validations == [result]


def test_record_style_compliance_fail_sets_pending_correction():
    quality = ConversationQuality()
    quality.record(ValidationResult(rule="style_compliance", severity="FAIL", confidence=0.9))
    assert quality.pending_style_correction is True


def test_record_style_compliance_info_does_not_set_pending_correction():
    quality = ConversationQuality()
    quality.record(ValidationResult(rule="style_compliance", severity="INFO", confidence=1.0))
    assert quality.pending_style_correction is False


def test_record_non_style_rule_fail_never_sets_pending_style_correction():
    """Architecture requirement: pending_style_correction is style-compliance
    specific -- a future PricingValidator/ShapeValidator FAIL must never
    trigger a language-correction instruction."""
    quality = ConversationQuality()
    quality.record(ValidationResult(rule="shape_compliance", severity="FAIL", confidence=1.0))
    assert quality.pending_style_correction is False


def test_clear_pending_style_correction():
    quality = ConversationQuality()
    quality.record(ValidationResult(rule="style_compliance", severity="FAIL", confidence=0.9))
    assert quality.pending_style_correction is True
    quality.clear_pending_style_correction()
    assert quality.pending_style_correction is False


def test_fresh_conversation_quality_has_no_validations_and_no_pending_correction():
    quality = ConversationQuality()
    assert quality.validations == []
    assert quality.pending_style_correction is False


def test_two_conversation_quality_instances_never_share_state():
    a = ConversationQuality()
    b = ConversationQuality()
    a.record(ValidationResult(rule="style_compliance", severity="FAIL", confidence=1.0))
    assert b.validations == []
    assert b.pending_style_correction is False
