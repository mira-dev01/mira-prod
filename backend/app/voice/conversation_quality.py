"""ConversationQuality -- the single, generic home for validator output and
system-health/compliance metrics. Purely observational: nothing in this
module ever influences what Mira says or how the pipeline behaves. It exists
for logging, analytics, production debugging, and future dashboards/adaptive
systems, exactly as scoped.

Architecture boundary (see conversation_state.py / conversation_style.py's
own module docstrings for the other two sides of this same boundary):
- ConversationState owns conversation FACTS (what's known, what's happening).
- ConversationStyle owns HOW Mira speaks (language, script, tone).
- ConversationQuality owns validator/system-health METRICS ONLY. It is not
  read by any prompt-construction code and not read by any validator to
  decide its own verdict -- validators WRITE here, they never read each
  other's history from here to make a decision. The one narrow exception is
  pending_style_correction, a single boolean read by exactly one consumer
  (state_prompt_sync.py) to decide whether to ask ConversationStyle -- never
  a validator directly -- to render a more emphatic version of the SAME
  style block. That is the one permitted, one-directional bridge between
  quality and behavior: a quality signal can prompt ConversationStyle to
  speak louder, but no validator ever writes prompt text itself.

No circular dependencies: this module may reference ConversationStyle's
types (for ValidationResult.metadata typing convenience only) but
ConversationStyle never imports this module, and neither ConversationState
nor ConversationStyle ever import ConversationQuality.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["INFO", "WARNING", "FAIL"]


@dataclass(frozen=True)
class ValidationResult:
    """Generic, reusable result shape for ANY validator -- language
    compliance today, pricing/property-reference/JSON/tool-usage/escalation/
    hallucination validators in the future, all emitting this same shape so
    logging, ConversationQuality.record, and any future dashboard can treat
    every validator uniformly instead of each inventing its own ad-hoc
    fields (the exact inconsistency this type replaces -- the old
    LanguageComplianceRule's RuleResult and response_shape_guard's bare
    list[str] of violation names were two different shapes for structurally
    the same kind of output)."""

    rule: str
    severity: Severity
    confidence: float
    metadata: dict = field(default_factory=dict)
    processing_time_ms: float = 0.0
    turn_index: int = 0

    @property
    def is_failure(self) -> bool:
        return self.severity == "FAIL"


@dataclass
class ConversationQuality:
    """Per-call system-health/compliance record. Constructed fresh alongside
    ConversationState/ConversationStyle at pipeline construction (same
    per-call lifetime as ConversationState -- not a DB table, not Redis,
    same non-goal memory-architecture-plan.md section 2.4 already
    established for ConversationState itself). Read at call-end for
    logging/future summary use; never read by any prompt-construction code
    except the one narrow pending_style_correction bridge described above.
    """

    validations: list[ValidationResult] = field(default_factory=list)
    # The ONE piece of cross-turn signal a validator is allowed to set,
    # consumed only by state_prompt_sync.py (read-only, one specific
    # consumer -- not a general read/write bus other processors reach into).
    pending_style_correction: bool = False

    def record(self, result: ValidationResult) -> None:
        self.validations.append(result)
        if result.rule == "style_compliance" and result.is_failure:
            self.pending_style_correction = True

    def clear_pending_style_correction(self) -> None:
        self.pending_style_correction = False
