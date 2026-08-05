"""Parses a host's free-text (typed or dictated) pricing ideology into
structured PropertyPricingRule drafts -- minimum-stay rules, length-of-stay
discounts, early check-in/late checkout fees, and freeform concessions.

Dictation reuses the existing DictationTextarea component / POST
/voice/transcribe endpoint unchanged (Sarvam batch STT, already wired for
every other dashboard text field) -- no new transcription code here, only
the same "field ends up as text" assumption every other DictationTextarea
call site already makes.

Reuses discount_policy_service.call_configured_llm_for_json for the actual
provider call (same Groq/Anthropic/OpenRouter selection, same "not built on
the streaming voice pipeline" reasoning -- see that module's docstring)
rather than duplicating the three provider-call functions here.

Every rule this produces lands with status="pending_validation" --
pricing_engine.py only ever reads status="approved" rows, same discipline
as HostDiscountRule (see memory-architecture-plan.md section 4). A parse
failure raises PricingPolicyParseError; callers must not silently fall back
to inventing a rule.
"""

import json
import logging

from app.models.property_pricing_rule import PropertyPricingRule
from app.services import discount_policy_service

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """You turn a short-term rental host's pricing policy, written or dictated in their \
own words, into structured pricing rules. Read the host's text and extract every distinct pricing rule \
you can find. For each rule, output:
- rule_type: one of "length_of_stay" (a discount for staying a minimum number of nights), \
"minimum_stay_nights" (a minimum number of nights required, generally or on weekends specifically), \
"early_checkin_fee" (a fee for checking in earlier than the standard time), "late_checkout_fee" (a fee \
for checking out later than the standard time), or "custom" (anything that doesn't fit those four).
- condition: an object describing the rule's trigger. For "length_of_stay", {"min_nights": N}. For \
"minimum_stay_nights", {"min_nights": N} for a general minimum, or {"weekend_min_nights": N} for a \
weekend-only minimum (a stay is "weekend" if it includes a Friday or Saturday night). For \
"early_checkin_fee"/"late_checkout_fee", {"fee": N} where N is the flat rupee amount. For "custom", \
whatever the host described, in your own best structured guess.
- discount_percent: for "length_of_stay" or "custom" discount-shaped rules, a number from 0 to 100. \
Omit (null) for "minimum_stay_nights"/"early_checkin_fee"/"late_checkout_fee" -- those aren't discounts.
- label: for "custom" rules only, a short plain-English description of what the host described. Omit \
(null) for the other four rule_types.

Only extract what the host actually said -- never invent a rule, a number, or a condition the host did \
not state or clearly imply. If the host's text has no extractable pricing rule, return an empty list.

Respond with ONLY a JSON object of this exact shape, no other text:
{"rules": [{"rule_type": "...", "condition": {}, "discount_percent": null, "label": null}]}

Host's pricing policy:
"""

_VALID_RULE_TYPES = {"length_of_stay", "minimum_stay_nights", "early_checkin_fee", "late_checkout_fee", "custom"}


class PricingPolicyParseError(Exception):
    pass


def _extract_json_rules(raw_text: str) -> list[dict]:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise PricingPolicyParseError(f"Model did not return valid JSON: {exc}") from exc

    rules = parsed.get("rules")
    if not isinstance(rules, list):
        raise PricingPolicyParseError("Model response missing a 'rules' list")

    cleaned = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rule_type = rule.get("rule_type")
        condition = rule.get("condition")
        if rule_type not in _VALID_RULE_TYPES or not isinstance(condition, dict):
            continue
        discount_percent = rule.get("discount_percent")
        if discount_percent is not None:
            if not isinstance(discount_percent, (int, float)) or not (0 <= discount_percent <= 100):
                continue
            discount_percent = float(discount_percent)
        label = rule.get("label")
        if label is not None and not isinstance(label, str):
            label = None
        cleaned.append(
            {"rule_type": rule_type, "condition": condition, "discount_percent": discount_percent, "label": label}
        )
    return cleaned


async def parse_pricing_policy_text(pricing_policy_text: str) -> list[dict]:
    """Returns a list of {"rule_type": str, "condition": dict,
    "discount_percent": float | None, "label": str | None} dicts -- never
    writes to the DB itself, callers turn these into PropertyPricingRule
    rows with status="pending_validation" and no properties selected yet
    (the host picks which properties each applies to before approving)."""
    prompt = _EXTRACTION_PROMPT + pricing_policy_text
    raw = await discount_policy_service.call_configured_llm_for_json(
        prompt,
        PricingPolicyParseError("No LLM provider is configured (GROQ_API_KEY/ANTHROPIC_API_KEY/OPENROUTER_API_KEY)"),
    )
    return _extract_json_rules(raw)


def build_pending_rules(host_id, pricing_policy_text: str, parsed_rules: list[dict]) -> list[PropertyPricingRule]:
    return [
        PropertyPricingRule(
            host_id=host_id,
            rule_type=rule["rule_type"],
            condition=rule["condition"],
            discount_percent=rule["discount_percent"],
            label=rule["label"],
            property_ids=[],
            source="ai_parsed",
            status="pending_validation",
            raw_source_text=pricing_policy_text,
        )
        for rule in parsed_rules
    ]
