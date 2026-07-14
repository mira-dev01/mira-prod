"""Parses a host's free-text discount policy paragraph
(User.discount_policy_text) into structured HostDiscountRule drafts.

Deliberately NOT built on app/voice/pipeline.py's _build_llm()/pipecat
services -- those are wired for the streaming, function-calling voice
pipeline (per-call health tracking, live-429 fallback mid-turn) and aren't a
fit for a single one-shot JSON-extraction call from a REST endpoint. This
uses the same provider SDKs directly (already transitive deps of
pipecat-ai[groq,anthropic]) with a simple one-model-then-fallback attempt,
which is all a low-frequency, host-triggered parse needs.

Every rule this produces lands with status="pending_validation" --
pricing_engine.negotiate_rate only ever reads status="approved" rows (see
memory-architecture-plan.md section 4). A parse failure raises
DiscountPolicyParseError; callers must not silently fall back to inventing
a rule.
"""

import json
import logging

from app.config import settings
from app.models.host_discount_rule import HostDiscountRule

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """You turn a short-term rental host's discount policy, written in their own \
words, into structured rules. Read the host's text and extract every distinct discount rule you can \
find. For each rule, output:
- trigger_type: one of "no_ask" (guest does not ask for a discount, price stays as first offered), \
"guest_requests" (guest asks for a discount / pushes back on price), "repeat_guest_same_host" (a guest \
who has stayed at this host's other properties before), or "custom" (anything that doesn't fit those \
three -- keep trigger_type as a short label in that case).
- discount_percent: a number from 0 to 100. If the host says "keep the price as offered" with no \
number, that trigger's discount_percent is 0.

Only extract what the host actually said -- never invent a rule, a trigger, or a percentage the host \
did not state or clearly imply. If the host's text has no extractable discount rule, return an empty \
list.

Respond with ONLY a JSON object of this exact shape, no other text:
{"rules": [{"trigger_type": "...", "discount_percent": 0}]}

Host's discount policy:
"""


class DiscountPolicyParseError(Exception):
    pass


def _extract_json_rules(raw_text: str) -> list[dict]:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise DiscountPolicyParseError(f"Model did not return valid JSON: {exc}") from exc

    rules = parsed.get("rules")
    if not isinstance(rules, list):
        raise DiscountPolicyParseError("Model response missing a 'rules' list")

    cleaned = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        trigger_type = rule.get("trigger_type")
        discount_percent = rule.get("discount_percent")
        if not isinstance(trigger_type, str) or not isinstance(discount_percent, (int, float)):
            continue
        if not (0 <= discount_percent <= 100):
            continue
        cleaned.append({"trigger_type": trigger_type, "discount_percent": float(discount_percent)})
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


async def parse_discount_policy_text(discount_policy_text: str) -> list[dict]:
    """Returns a list of {"trigger_type": str, "discount_percent": float}
    dicts -- never writes to the DB itself, callers turn these into
    HostDiscountRule rows with status="pending_validation"."""
    prompt = _EXTRACTION_PROMPT + discount_policy_text

    if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
        raw = await _call_anthropic(prompt)
    elif settings.groq_api_key:
        raw = await _call_groq(prompt)
    elif settings.openrouter_api_key:
        raw = await _call_openrouter(prompt)
    else:
        raise DiscountPolicyParseError("No LLM provider is configured (GROQ_API_KEY/ANTHROPIC_API_KEY/OPENROUTER_API_KEY)")

    return _extract_json_rules(raw)


def build_pending_rules(host_id, discount_policy_text: str, parsed_rules: list[dict]) -> list[HostDiscountRule]:
    return [
        HostDiscountRule(
            host_id=host_id,
            trigger_type=rule["trigger_type"],
            discount_percent=rule["discount_percent"],
            source="ai_parsed",
            status="pending_validation",
            raw_source_text=discount_policy_text,
        )
        for rule in parsed_rules
    ]
