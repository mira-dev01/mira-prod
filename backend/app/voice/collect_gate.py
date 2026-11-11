"""The host's "must have" details, enforced where it can be: in the tools.

The Agent page (schemas/agent_config.py) lets a host mark guest details as
Off / Nice to have / Must have. The prompt tells Mira what to ask; this module
is the deterministic backstop that makes "must have" mean something. Before a
step that needs a detail, the tool says "ask the guest for X first" instead of
running.

Rules (also what the Agent page promises the host):
  * Name and phone are needed before availability, pricing or a discount.
    Every other must-have is needed before the step it belongs to.
  * A detail is asked for at most twice. The gate says "ask" on the first two
    separate guest turns; on the third attempt the detail is waived and the
    step runs without it. Repeated attempts inside one guest turn are one ask.
  * The gate never stops a lead being saved: callers still record the guest's
    interest (lead + temperature) when they block.

Pure functions over ConversationState and AgentConfig, so the policy is unit
tested without a live call.
"""

from dataclasses import dataclass, field
from typing import Literal

from app.schemas.agent_config import AgentConfig
from app.voice.conversation_state import ConversationState, _dates_known

MAX_NUDGES = 2

# recommend: understanding what the guest needs, before suggesting places.
# funnel: availability / pricing / discount for a specific property.
Stage = Literal["recommend", "funnel"]

# detail -> how the agent should refer to it when told to ask.
LABELS = {
    "guest_name": "their name",
    "phone": "their phone number",
    "dates": "their stay dates (or how many nights)",
    "guests": "how many guests",
    "purpose": "the reason for the stay",
    "location": "the area they want to stay in",
    "budget": "their budget",
    "email": "their email address",
}

_COLLECT_FIELDS_BY_STAGE: dict[Stage, tuple[str, ...]] = {
    "recommend": ("dates", "guests", "purpose", "location", "budget"),
    "funnel": ("dates", "guests", "purpose", "location", "budget", "email"),
}


CUSTOM_PREFIX = "custom:"


def label_of(detail: str, config: AgentConfig | None = None) -> str:
    """How the agent should refer to a detail: the built-in wording, or the host's own label for a custom one."""
    if detail.startswith(CUSTOM_PREFIX):
        for field in (config.collect.custom if config else []):
            if f"{CUSTOM_PREFIX}{field.id}" == detail:
                return field.label
        return detail[len(CUSTOM_PREFIX):].replace("_", " ")
    return LABELS[detail]


def _known(detail: str, state: ConversationState) -> bool:
    slots = state.slots
    if detail.startswith(CUSTOM_PREFIX):
        return slots.get(detail) not in (None, "")
    if detail == "dates":
        return _dates_known(slots)
    slot = {
        "guest_name": "guest_name",
        "phone": "phone",
        "guests": "num_guests",
        "purpose": "purpose_of_stay",
        "location": "preferred_location",
        "budget": "budget",
        "email": "email",
    }[detail]
    return slots.get(slot) not in (None, "")


def required_details(config: AgentConfig, stage: Stage) -> list[str]:
    """Details that must be known before `stage`, in the order to ask."""
    details = ["guest_name", "phone"] if stage == "funnel" else []
    collect = config.collect
    details += [name for name in _COLLECT_FIELDS_BY_STAGE[stage] if getattr(collect, name) == "required"]
    if stage == "funnel":
        # The host's own must-haves come before availability or a price, like email does.
        details += [f"{CUSTOM_PREFIX}{field.id}" for field in collect.custom if field.mode == "required"]
    return details


def anything_missing(
    state: ConversationState, config: AgentConfig, stage: Stage, provided: frozenset[str] = frozenset()
) -> bool:
    """Cheap, side-effect-free check: lets a caller skip any lookup work when
    every needed detail is already known (the common case)."""
    return any(d not in provided and not _known(d, state) for d in required_details(config, stage))


@dataclass
class GateDecision:
    block: bool
    text: str = ""
    # Details still needed (blocking) and details just waived after two asks.
    ask_for: list[str] = field(default_factory=list)
    waived: list[str] = field(default_factory=list)


def evaluate(
    state: ConversationState, config: AgentConfig, stage: Stage, provided: frozenset[str] = frozenset()
) -> GateDecision:
    """Decides whether the tool may run now. Updates the nudge counters.
    `provided` names details the calling tool was itself handed this turn
    (e.g. num_guests as an argument) that are not in the state yet."""
    missing = [
        d for d in required_details(config, stage) if d not in provided and not _known(d, state)
    ]
    if not missing:
        return GateDecision(block=False)

    ask_for: list[str] = []
    waived: list[str] = []
    for detail in missing:
        # Waived only once two asks have been used up AND this is a later guest
        # turn than the second ask, so a retry inside that turn still asks.
        used_up = state.collect_nudges.get(detail, 0) >= MAX_NUDGES
        if used_up and state.collect_nudge_turn.get(detail) != state.turn_index:
            waived.append(detail)
        else:
            ask_for.append(detail)

    for detail in waived:
        if detail not in state.waived_fields:
            state.waived_fields.append(detail)

    if not ask_for:
        return GateDecision(block=False, waived=waived)

    # One ask per guest turn: a retry inside the same turn repeats the
    # instruction without using up one of the two.
    for detail in ask_for:
        if state.collect_nudge_turn.get(detail) != state.turn_index:
            state.collect_nudges[detail] = state.collect_nudges.get(detail, 0) + 1
            state.collect_nudge_turn[detail] = state.turn_index

    names = " and ".join(label_of(d, config) for d in ask_for)
    first = label_of(ask_for[0], config)
    text = (
        f"Not yet -- the host needs {names} before this step. Ask the guest for {first} now"
        + (", then the rest one at a time" if len(ask_for) > 1 else "")
        + ", in your own words, and do not call this tool again until they have answered. "
        "If they have already told you earlier in this call, save it with update_lead and try again instead of "
        "asking twice. Do not mention a price or availability yet."
    )
    return GateDecision(block=True, text=text, ask_for=ask_for, waived=waived)


def waived_note(waived: list[str], config: AgentConfig | None = None) -> str:
    """Appended to a tool result that ran without a must-have detail."""
    if not waived:
        return ""
    names = ", ".join(label_of(d, config) for d in waived)
    return f"\n(The guest did not give {names} after being asked. Carry on without it; do not ask again.)"
