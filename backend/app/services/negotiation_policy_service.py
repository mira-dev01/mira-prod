"""Parses a host's free-text (typed or dictated) negotiation/pricing policy
into structured NegotiationRule drafts -- host-wide discount triggers (no
guest ask, guest asks, repeat guest) AND stay-pricing rules (minimum-stay,
length-of-stay discount, early check-in/late checkout fees, freeform
concessions), all from one paragraph and one extraction call. Replaces what
used to be two separate services (discount_policy_service,
pricing_policy_service) parsing two separate tables -- hosts were describing
both concepts ("how should the agent negotiate") in the same breath, so one
prompt covering both rule families removes the need to fill in two different
text boxes for what is, to the host, one policy.

Deliberately NOT built on app/voice/pipeline.py's _build_llm()/pipecat
services -- those are wired for the streaming, function-calling voice
pipeline (per-call health tracking, live-429 fallback mid-turn) and aren't a
fit for a single one-shot JSON-extraction call from a REST endpoint. Uses the
same provider SDKs directly with a simple one-model-then-fallback attempt,
which is all a low-frequency, host-triggered parse needs.

Every rule this produces lands with status="pending_validation" --
pricing_engine.py only ever reads status="approved" rows. A parse failure
raises NegotiationPolicyParseError; callers must not silently fall back to
inventing a rule.
"""

import json
import logging

from app.config import settings
from app.models.negotiation_rule import NegotiationRule

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """You turn a short-term rental host's negotiation and pricing policy, written or \
dictated in their own words, into structured rules. Read the host's text and extract every distinct rule \
you can find -- both how the agent should negotiate discounts, AND any stay-pricing rules (minimum stays, \
length-of-stay discounts, fees). For each rule, output:
- rule_type: one of:
  - "discount_no_ask" (what discount, if any, to offer when a guest does NOT ask for one -- price stays \
as first offered unless the host says otherwise)
  - "discount_guest_requests" (discount to offer when a guest asks for one / pushes back on price)
  - "discount_repeat_guest" (discount for a guest who has stayed at this host's other properties before)
  - "length_of_stay" (a discount for staying a minimum number of nights)
  - "minimum_stay_nights" (a minimum number of nights required, generally or on weekends specifically)
  - "early_checkin_fee" (a fee for checking in earlier than the standard time)
  - "late_checkout_fee" (a fee for checking out later than the standard time)
  - "custom" (anything that doesn't fit those seven -- keep a short label describing it)
- condition: an object describing the rule's trigger. Empty {} for the three discount_* trigger types \
(their percentage is the whole rule). For "length_of_stay", {"min_nights": N}. For "minimum_stay_nights", \
{"min_nights": N} for a general minimum, or {"weekend_min_nights": N} for a weekend-only minimum (a stay \
is "weekend" if it includes a Friday or Saturday night). For "early_checkin_fee"/"late_checkout_fee", \
{"fee": N} where N is the flat rupee amount. For "custom", whatever the host described, in your own best \
structured guess.
- discount_percent: for the three discount_* types, "length_of_stay", or a discount-shaped "custom" rule, \
a number from 0 to 100, ONLY when the host described a SINGLE value for that rule. If the host says "keep \
the price as offered" with no number, that's 0. Omit (null) for "minimum_stay_nights"/"early_checkin_fee"/\
"late_checkout_fee" -- those aren't discounts. Also omit (null) whenever "stages" below is used instead.
- stages: an ORDERED LIST of {"order": N, "value": X} objects, used ONLY for "discount_guest_requests" or \
"custom" rules where the host describes a PROGRESSION of DIFFERENT values across repeated guest pushback \
-- e.g. "I start at 3%, and if they push back I can go to 5%, and if they still push I can go to 7%" \
becomes stages [{"order":0,"value":3},{"order":1,"value":5},{"order":2,"value":7}]. order starts at 0 and \
increases in the sequence the host described, however many steps they actually described (never assume a \
specific count). Every stage's value must be a number the host actually stated for that step -- never \
invent an intermediate value the host didn't say, never round to a "nicer" number, and never pad the \
sequence with a guessed final value. Omit (null) whenever the host describes only ONE value for a rule \
(use discount_percent instead) -- a single flat number is NEVER stages of length 1; only use stages when \
the host's own words describe at least two DISTINCT sequential values tied to repeated guest pushback. If \
the host's wording about a progression is ambiguous (e.g. you cannot tell how many distinct steps they \
mean, or whether two mentioned numbers are steps in one sequence or two separate rules), do NOT guess -- \
extract only the single value you're confident about (as discount_percent) and leave stages null, or, if \
no single value is confidently extractable either, drop that rule entirely rather than inventing one.
- label: for "custom" rules only, a short plain-English description of what the host described. Omit \
(null) for the other seven rule_types.

Only extract what the host actually said -- never invent a rule, a trigger, a number, a stage, or a \
condition the host did not state or clearly imply. If the host's text has no extractable rule, return an \
empty list.

Respond with ONLY a JSON object of this exact shape, no other text:
{"rules": [{"rule_type": "...", "condition": {}, "discount_percent": null, "stages": null, "label": null}]}

Host's negotiation and pricing policy:
"""

_VALID_RULE_TYPES = {
    "discount_no_ask",
    "discount_guest_requests",
    "discount_repeat_guest",
    "length_of_stay",
    "minimum_stay_nights",
    "early_checkin_fee",
    "late_checkout_fee",
    "custom",
}


class NegotiationPolicyParseError(Exception):
    pass


# Phase 4E: the only two rule_types a progressive stage sequence can
# attach to -- "discount_guest_requests" (a host-wide "if they push back"
# ladder) and "custom" (a property-scoped one, e.g. "for Property A I go
# 5% then 10%"). Every other rule_type either isn't a discount at all
# (minimum_stay_nights/early_checkin_fee/late_checkout_fee) or has no
# concept of guest pushback to progress against (discount_no_ask is what
# to offer with NO ask; discount_repeat_guest is loyalty-based, not
# pushback-based -- see this module's own docstring update below on why
# loyalty is deliberately NOT staged by this parser). Matches
# NegotiationRule.stages' own docstring, which allows stages on any
# rule_type at the schema level -- this is a parser-level, not a
# schema-level, restriction: nothing stops a host from directly editing a
# discount_repeat_guest rule's stages via the API, but this parser will
# never GENERATE one, since no natural-language pattern in the extraction
# prompt asks for it.
_STAGEABLE_RULE_TYPES = {"discount_guest_requests", "custom"}


def _clean_stages(raw_stages: object) -> list[dict] | None:
    """Validates a parsed `stages` value with the same fail-closed
    discipline as discount_percent above -- anything malformed is treated
    as "the model didn't actually give us a usable stage list" (returns
    None, falling back to the flat discount_percent path) rather than
    raising or passing through a shape pricing_engine.py can't safely
    read. A single-entry list is deliberately collapsed to None (not kept
    as a length-1 "stages") -- Phase 4B/4C's own generalized model already
    treats stages=None and a one-stage list as behaviorally identical, so
    a length-1 stages list adds nothing but ambiguity about whether the
    host actually meant a progression; the extraction prompt already
    instructs against producing one, this is the defensive backstop for
    when the model does anyway."""
    if not isinstance(raw_stages, list) or len(raw_stages) < 2:
        return None
    cleaned_stages = []
    seen_orders = set()
    for stage in raw_stages:
        if not isinstance(stage, dict):
            return None
        order = stage.get("order")
        value = stage.get("value")
        # Self-review fix: JSON's own number type doesn't distinguish
        # int/float, so an LLM emitting well-formed JSON may legitimately
        # write "order": 0.0 instead of 0 -- Python's json module then
        # parses that as a float, which the original `isinstance(order,
        # int)` check rejected outright, dropping an otherwise-valid
        # staged rule over pure formatting. NegotiationStage (the Pydantic
        # schema this data eventually round-trips through,
        # app/schemas/negotiation_rule.py) already coerces an integer-
        # valued float order to int without complaint -- this parser was
        # stricter than the schema that actually consumes the same data,
        # for no real benefit. Now accepts any bool-excluded int/float
        # whose value is a whole number, matching the schema's own
        # tolerance, while still rejecting a genuinely fractional order
        # (e.g. 0.5), which has no sensible meaning as a sequence position.
        if isinstance(order, bool) or not isinstance(order, (int, float)) or order < 0 or order != int(order):
            return None
        order = int(order)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not (0 <= value <= 100):
            return None
        if order in seen_orders:
            return None
        seen_orders.add(order)
        cleaned_stages.append({"order": order, "value": float(value)})
    return sorted(cleaned_stages, key=lambda s: s["order"])


def _extract_json_rules(raw_text: str) -> list[dict]:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise NegotiationPolicyParseError(f"Model did not return valid JSON: {exc}") from exc

    rules = parsed.get("rules")
    if not isinstance(rules, list):
        raise NegotiationPolicyParseError("Model response missing a 'rules' list")

    cleaned = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rule_type = rule.get("rule_type")
        condition = rule.get("condition")
        if rule_type not in _VALID_RULE_TYPES or not isinstance(condition, dict):
            continue

        stages = _clean_stages(rule.get("stages")) if rule_type in _STAGEABLE_RULE_TYPES else None

        discount_percent = rule.get("discount_percent")
        if discount_percent is not None:
            if not isinstance(discount_percent, (int, float)) or not (0 <= discount_percent <= 100):
                continue
            discount_percent = float(discount_percent)
        # Per the ratified Phase 4C decision (staged supersedes flat for the
        # same rule_type/scope): if a usable stages list was extracted,
        # discount_percent is never set alongside it on the SAME drafted
        # row -- avoids drafting a row whose two action-value fields
        # disagree about what the rule authorizes before a host has even
        # reviewed it. discount_percent stays whatever was extracted only
        # when stages is None.
        if stages is not None:
            discount_percent = None

        label = rule.get("label")
        if label is not None and not isinstance(label, str):
            label = None
        cleaned.append(
            {
                "rule_type": rule_type,
                "condition": condition,
                "discount_percent": discount_percent,
                "stages": stages,
                "label": label,
            }
        )
    return cleaned


async def _call_groq(prompt: str) -> str:
    from groq import AsyncGroq

    client = AsyncGroq(api_key=settings.groq_api_key)
    response = await client.chat.completions.create(
        model=settings.groq_model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


async def _call_anthropic(prompt: str) -> str:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


async def _call_openrouter(prompt: str) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openrouter_api_key, base_url="https://openrouter.ai/api/v1")
    response = await client.chat.completions.create(
        model=settings.openrouter_model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


async def call_configured_llm_for_json(prompt: str, no_provider_error: Exception) -> str:
    """Shared one-shot JSON-extraction call, same provider-selection order
    this module's own parse_negotiation_policy_text uses. Kept as a public
    helper (other one-shot JSON extraction call sites in this codebase
    reused its predecessor, discount_policy_service.call_configured_llm_for_json,
    the same way) so any future one-shot extraction doesn't need a third
    copy of this provider-selection plumbing. Raises no_provider_error (the
    caller's own exception type) when nothing is configured, so each
    caller's error message/type stays specific to what it was trying to do.
    """
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
        return await _call_anthropic(prompt)
    if settings.groq_api_key:
        return await _call_groq(prompt)
    if settings.openrouter_api_key:
        return await _call_openrouter(prompt)
    raise no_provider_error


async def parse_negotiation_policy_text(policy_text: str) -> list[dict]:
    """Returns a list of {"rule_type": str, "condition": dict,
    "discount_percent": float | None, "stages": list[dict] | None,
    "label": str | None} dicts -- never writes to the DB itself, callers
    turn these into NegotiationRule rows with status="pending_validation".
    stages (Phase 4E) is populated only for a "discount_guest_requests"/
    "custom" rule where the host described a genuine multi-step pushback
    progression (see _clean_stages) -- None for every flat rule, matching
    NegotiationRule.stages' own "None = flat, unchanged behavior" default."""
    prompt = _EXTRACTION_PROMPT + policy_text
    raw = await call_configured_llm_for_json(
        prompt,
        NegotiationPolicyParseError(
            "No LLM provider is configured (GROQ_API_KEY/ANTHROPIC_API_KEY/OPENROUTER_API_KEY)"
        ),
    )
    return _extract_json_rules(raw)


def build_pending_rules(host_id, policy_text: str, parsed_rules: list[dict]) -> list[NegotiationRule]:
    return [
        NegotiationRule(
            host_id=host_id,
            rule_type=rule["rule_type"],
            condition=rule["condition"],
            discount_percent=rule["discount_percent"],
            stages=rule.get("stages"),
            label=rule["label"],
            property_ids=[],
            source="ai_parsed",
            status="pending_validation",
            raw_source_text=policy_text,
        )
        for rule in parsed_rules
    ]
