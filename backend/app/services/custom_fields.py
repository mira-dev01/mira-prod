"""The host's own "also ask for" details (Agent page -> Collect -> custom), made concrete.

Three jobs, all pure functions so they are tested without a live call:
  * parse_answers: what the agent hands update_lead ("arrival_time: 3 PM") -> clean saved values;
  * label/kind lookups the collect gate and the prompt share;
  * hot-rule checks (lead_temperature uses them): does this lead meet a host's hot condition?

An answer the type cannot hold is refused with a plain reason the agent can act on (re-ask), never
stored half-right: a "number" that is not a number, a "yes/no" that is neither.
"""

import re
from dataclasses import dataclass
from datetime import date

from app.schemas.agent_config import AgentConfig, CustomField, HotRule

MAX_VALUE_CHARS = 200

_YES = {"yes", "yeah", "yep", "yup", "y", "haan", "han", "ha", "ji", "hanji", "haanji", "sure", "ok", "okay", "correct", "true"}
_NO = {"no", "nope", "nah", "n", "nahi", "nahin", "na", "false"}
_TIME_RE = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


@dataclass(frozen=True)
class Answer:
    id: str
    label: str
    value: str  # what is stored and shown


@dataclass(frozen=True)
class ParseResult:
    accepted: list[Answer]
    problems: list[str]  # plain sentences for the agent: unknown field, or "X needs a number"


def by_id(config: AgentConfig) -> dict[str, CustomField]:
    return {f.id: f for f in config.collect.custom}


def normalize(field: CustomField, raw: str) -> str | None:
    """The value to store, or None when `raw` cannot be that kind of answer."""
    text = re.sub(r"\s+", " ", (raw or "")).strip().strip("\"'")[:MAX_VALUE_CHARS]
    if not text:
        return None
    if field.kind == "text":
        return text
    if field.kind == "yes_no":
        words = re.findall(r"[a-z]+", text.lower())
        if not words:
            return None
        first = words[0]
        if first in _YES:
            return "Yes"
        if first in _NO:
            return "No"
        return None
    if field.kind == "number":
        match = _NUMBER_RE.search(text)
        if not match:
            return None
        number = float(match.group(0).replace(",", ""))
        return str(int(number)) if number == int(number) else str(number)
    if field.kind == "time":
        # Keep what the guest said when it is a loose answer ("evening", "after lunch"); tidy a clock time.
        match = _TIME_RE.search(text)
        if match and (match.group(3) or match.group(2) is not None):
            hour, minute, suffix = int(match.group(1)), match.group(2) or "00", (match.group(3) or "").lower().replace(".", "")
            if 0 <= hour <= 23 and 0 <= int(minute) <= 59:
                if suffix:
                    return f"{hour}:{minute} {suffix.upper()}" if 1 <= hour <= 12 else None
                return f"{hour:02d}:{minute}"
        return text
    return text


def parse_answers(config: AgentConfig, answers: list[str] | None) -> ParseResult:
    """`answers` are the model's "field_id: value" strings."""
    fields = by_id(config)
    accepted: dict[str, Answer] = {}
    problems: list[str] = []
    for item in answers or []:
        field_id, sep, raw = str(item).partition(":")
        field_id = field_id.strip().lower()
        if not sep:
            problems.append(f"Could not read {item!r}: write each answer as 'field_id: value'.")
            continue
        field = fields.get(field_id)
        if field is None:
            known = ", ".join(fields) or "none"
            problems.append(f"There is no detail called {field_id!r} (the ones to save are: {known}).")
            continue
        value = normalize(field, raw)
        if value is None:
            expects = {
                "number": "a number",
                "yes_no": "a yes or no",
                "time": "a time",
                "text": "an answer",
            }[field.kind]
            problems.append(f"{field.label} needs {expects}: ask the guest again.")
            continue
        accepted[field.id] = Answer(field.id, field.label, value)
    return ParseResult(list(accepted.values()), problems)


def details_for_storage(answers: list[Answer]) -> dict[str, dict[str, str]]:
    """{id: {"label", "value"}}: the label is kept with the value so a later rename or removal of the
    field never leaves an answer on a lead that nobody can read."""
    return {a.id: {"label": a.label, "value": a.value} for a in answers}


def slot_key(field_id: str) -> str:
    return f"custom:{field_id}"


# -- hot rules -------------------------------------------------------------------------


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rule_matches(
    rule: HotRule,
    *,
    guests: int | None,
    nights: int | None,
    budget: float | None,
    days_until_checkin: int | None,
    custom: dict[str, dict[str, str]] | None,
) -> bool:
    """Whether one host rule holds for what is known about the lead. An unknown value never matches
    (a lead is not hot for a number nobody has given)."""
    if rule.field.startswith("custom:"):
        entry = (custom or {}).get(rule.field[len("custom:"):])
        if not entry:
            return False
        raw = entry.get("value")
        known = 1.0 if raw == "Yes" else 0.0 if raw == "No" else _number(raw)
    else:
        known = {"guests": guests, "nights": nights, "budget": budget, "days_until_checkin": days_until_checkin}[rule.field]
        known = _number(known)
    if known is None:
        return False
    if rule.op == "gte":
        return known >= rule.value
    if rule.op == "lte":
        return known <= rule.value
    return known == rule.value


def any_rule_matches(rules: list[HotRule], lead_check_in: date | None, lead_check_out: date | None, *, guests, budget, custom, today: date) -> bool:
    nights = (lead_check_out - lead_check_in).days if lead_check_in and lead_check_out else None
    days_until = (lead_check_in - today).days if lead_check_in else None
    return any(
        rule_matches(r, guests=guests, nights=nights, budget=budget, days_until_checkin=days_until, custom=custom) for r in rules
    )
