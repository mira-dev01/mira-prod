"""What a host's approved policies say about *one property* on the topics a
guest's plans depend on -- parties/events and pets -- as a small, derived fact:
allowed, limited (with an optional guest cap) or not allowed.

Derived, never stored: the policy rows stay the single source of truth, so a
label on a property card, the recommendation filter and the knowledge-base fact
Mira speaks can never disagree -- edit or delete the policy and all three move.

Deliberately conservative: when the wording does not clearly say one of the
three, the stance is None ("nothing on file") and nothing is filtered on it.
A host-wide policy whose text names specific properties ("Parties only at
Casa Blu") is read per property: the named ones follow it, the others are
excluded by an "only", and otherwise unknown. A policy a review note flagged as
naming a place that isn't the host's (and that names none of their properties)
is not carried by any property, so it never becomes a label or a filter.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Literal

from app.services.policy_service import applicable, mentions_property

StanceKind = Literal["allowed", "limited", "not_allowed"]

PARTIES = "parties_events"
PETS = "pets"
STANCE_TOPICS = (PARTIES, PETS)

# A group at least this big is asked what the occasion is (when the host's
# properties have party rules at all), and sees each property's party rule
# beside the recommendation even if the guest never said "party".
GATHERING_ASK_MIN_GUESTS = 8


@dataclass(frozen=True)
class Stance:
    topic: str
    kind: StanceKind
    max_guests: int | None
    text: str  # the host's own words, spoken verbatim as the supporting fact


@dataclass(frozen=True)
class Label:
    key: str  # "<topic>:<kind>"
    label: str


_LIMITED = re.compile(
    r"\b(small|family|intimate|private|quiet)\s+(gatherings?|parties|groups?|celebrations?|functions?|events?)\b"
    r"|\bgatherings?\s+only\b|\bonly\s+small\b|\bwith\s+(prior\s+)?(approval|permission|notice)\b"
    r"|\b(on|upon)\s+request\b|\bsubject\s+to\b|\bat\s+(the\s+)?host'?s\s+discretion\b|\bconditions?\s+apply\b"
    r"|\bup\s*to\s+\d+\b|\b(max(imum)?|limit(ed)?)\s+(of\s+|to\s+)?\d+\b|\b(no\s+more|not\s+more)\s+than\s+\d+\b",
    re.IGNORECASE,
)
_NOT_ALLOWED = re.compile(
    r"\b(not|never)\s+(be\s+)?(allowed|permitted|welcome|accepted|entertained)\b|n't\s+(be\s+)?(allowed|permitted|welcome)\b"
    r"|\bno\s+(parties|party|events?|gatherings?|pets?|dogs?|animals?)\b|\bprohibited\b|\bbanned\b|\bstrictly\s+no\b"
    r"|\bpets?\s+are\s+not\b",
    re.IGNORECASE,
)
_ALLOWED = re.compile(
    r"\b(are|is)\s+(welcome|allowed|permitted|fine|ok|okay)\b|\b(allowed|permitted|welcome)\b|\bcan\s+(host|have|bring)\b",
    re.IGNORECASE,
)
_ONLY = re.compile(r"\bonly\b", re.IGNORECASE)
_MAX_GUESTS = [
    re.compile(
        r"\b(?:up\s*to|upto|max(?:imum)?(?:\s+of)?|limit(?:ed)?\s+to|not\s+more\s+than|no\s+more\s+than|at\s+most)\s*(\d{1,3})\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(\d{1,3})\s*(?:guests?|people|persons|pax|adults)\s*(?:max(?:imum)?|only|or\s+(?:fewer|less))\b", re.IGNORECASE),
]


def classify(text: str) -> tuple[StanceKind | None, int | None]:
    """The stance a piece of policy text states, and a guest cap if it gives one."""
    text = text or ""
    max_guests = next((int(m.group(1)) for pattern in _MAX_GUESTS if (m := pattern.search(text))), None)
    if _LIMITED.search(text):
        return "limited", max_guests
    if _NOT_ALLOWED.search(text):
        return "not_allowed", None
    if _ALLOWED.search(text):
        return "allowed", None
    return None, None


def _stance_from_policy(policy, property_, siblings) -> tuple[StanceKind | None, int | None]:
    kind, cap = classify(policy.text)
    if policy.property_id is not None:
        return kind, cap  # already this property's own policy
    named = {s.id for s in siblings if mentions_property(policy.text, s)}
    if not named:
        return kind, cap  # says nothing about particular properties: everyone
    if property_.id in named:
        # "Parties only at Casa Blu": the one it names is the one that's allowed.
        if kind is None and _ONLY.search(policy.text):
            return "allowed", None
        return kind, cap
    # Names other properties but not this one (applicable() only lets this
    # through for an "only" rule): everyone else is excluded.
    if _ONLY.search(policy.text) and kind in ("allowed", None):
        return "not_allowed", None
    return None, None


def stance_for(topic: str, active_policies: list, property_, siblings: list) -> Stance | None:
    """`active_policies`: every ACTIVE policy of the host; `siblings`: all the
    host's properties (including this one)."""
    for policy in applicable(active_policies, property_, siblings):
        if policy.topic != topic:
            continue
        kind, cap = _stance_from_policy(policy, property_, siblings)
        if kind is not None:
            return Stance(topic=topic, kind=kind, max_guests=cap, text=policy.text)
    return None


def stances_for_host(active_policies: list, properties: list, topic: str) -> dict[uuid.UUID, Stance | None]:
    return {p.id: stance_for(topic, active_policies, p, properties) for p in properties}


_LABELS: dict[tuple[str, str], str] = {
    (PARTIES, "allowed"): "Parties welcome",
    (PARTIES, "not_allowed"): "No parties",
    (PETS, "allowed"): "Pets allowed",
    (PETS, "limited"): "Pets with conditions",
    (PETS, "not_allowed"): "No pets",
}


def labels_for_property(active_policies: list, property_, siblings: list) -> list[Label]:
    labels = []
    for topic in STANCE_TOPICS:
        stance = stance_for(topic, active_policies, property_, siblings)
        if stance is None:
            continue
        if topic == PARTIES and stance.kind == "limited":
            text = f"Parties up to {stance.max_guests} guests" if stance.max_guests else "Small gatherings only"
        else:
            text = _LABELS[(topic, stance.kind)]
        labels.append(Label(key=f"{topic}:{stance.kind}", label=text))
    return labels


# -- recommendation ---------------------------------------------------------


@dataclass(frozen=True)
class GatheringFit:
    """How one property fits a guest who wants to host a party/event."""

    eligible: bool
    tier: int  # 0 = host says yes, 1 = yes with conditions, 2 = nothing on file
    note: str  # spoken-safe: only the host's own words, or an explicit "nothing on file"


def gathering_fit(stance: Stance | None, party_size: int | None) -> GatheringFit:
    if stance is None:
        return GatheringFit(
            True, 2, "No party or event policy is on file for this property -- do not say it allows them; offer to check with the host."
        )
    if stance.kind == "not_allowed":
        return GatheringFit(False, 3, f"Host policy on parties and events: {stance.text}")
    if stance.kind == "limited":
        if stance.max_guests is not None and party_size is not None and party_size > stance.max_guests:
            return GatheringFit(False, 3, f"Host policy on parties and events: {stance.text}")
        return GatheringFit(True, 1, f"Host policy on parties and events: {stance.text}")
    return GatheringFit(True, 0, f"Host policy on parties and events: {stance.text}")


async def host_has_party_rules(db, host_user_id: uuid.UUID, properties: list) -> bool:
    """True when at least one of the host's properties has an approved
    party/event policy that reads as a stance. Decides whether the Lead prompt
    asks about the occasion at all. Fails open (False): no rule, no question."""
    from sqlalchemy import select

    from app.models.policy import Policy

    try:
        policies = list(
            (await db.scalars(select(Policy).where(Policy.user_id == host_user_id, Policy.status == "active"))).all()
        )
    except Exception:
        await db.rollback()
        return False
    return any(v is not None for v in stances_for_host(policies, properties, PARTIES).values())
