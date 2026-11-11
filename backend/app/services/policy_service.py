"""The host's booking and house policies, and how they reach the agent.

A policy is one rule in the host's words ("Pets are not allowed"). It is either
host-wide (property_id NULL) or a property's own version that replaces the
host-wide rule of the same topic for that property only. The agent never reads
this table: approved policies are turned into knowledge-base facts by
property/chunking.py (build_fact_chunks) and found through search_faq like
everything else, so "nothing is spoken unless it is on file" holds for them too.

Rules that keep it honest:
- Only status="active" policies reach the agent. Anything read from an uploaded
  document is a draft until the host approves it.
- Every write that changes what a property would say re-syncs that property's
  facts straight away (a host-wide change re-syncs all of the host's
  properties), so an edit is live on the next question, not "eventually".
- One active policy per topic per scope (enforced by partial unique indexes);
  "other" is free-form and may repeat.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.policy import Policy, PolicyDocument
from app.models.property import Property
from app.models.user import User
from app.services import policy_documents

logger = logging.getLogger(__name__)

OTHER = "other"
# (key, label, what it covers) -- also what the host sees in the dashboard.
TOPICS: list[tuple[str, str, str]] = [
    ("check_in_out", "Check-in & check-out", "Times, early arrival, late departure, key handover"),
    ("cancellation", "Cancellation & refunds", "Cancellation windows, refunds, no-shows"),
    ("payment", "Payment", "Advance, balance due dates, accepted methods"),
    ("damage_deposit", "Damage & security deposit", "Deposit amount, damage charges"),
    ("booking_limits", "Booking limits", "Minimum and maximum stay, how far ahead to book, guest limits"),
    ("age_restrictions", "Age restrictions", "Minimum age to book or check in, children, infants"),
    ("id_requirements", "ID & documents", "ID proof, foreign guests, registration"),
    ("pets", "Pets", "Whether pets are allowed, fees, limits"),
    ("smoking", "Smoking & alcohol", "Smoking, alcohol, drugs"),
    ("parties_events", "Parties & events", "Parties, events, music, gatherings"),
    ("visitors", "Visitors & extra guests", "Day visitors, extra guests, extra-person fees"),
    ("quiet_hours", "Quiet hours & noise", "Quiet hours, noise, curfew"),
    (OTHER, "Other", "Anything else guests should know"),
]
TOPIC_LABELS = {key: label for key, label, _ in TOPICS}

# What puts a sentence of free text (an imported house-rules paragraph) on a
# policy topic. Used only to decide whether an approved policy already speaks
# for that sentence, so it is dropped from the knowledge base rather than
# contradicting the policy (see topics_in and property/chunking.py).
_TOPIC_PATTERNS: dict[str, re.Pattern[str]] = {
    topic: re.compile(pattern, re.IGNORECASE)
    for topic, pattern in {
        "check_in_out": r"check[- ]?(in|out)|early arrival|late departure",
        "cancellation": r"\bcancel|\brefund|no[- ]show",
        "payment": r"\badvance\b|\bpayment|\bupi\b|balance due",
        "damage_deposit": r"security deposit|\bdamage",
        "booking_limits": r"minimum (stay|nights?)|maximum (stay|guests?)|\bmin(imum)? stay|\bmax(imum)? guests?",
        "age_restrictions": r"minimum age|\binfants?\b|\bchildren\b|\bkids\b|years old|age limit",
        "id_requirements": r"\bid proof|photo id|aadh?aa?r|passport|government id|valid id",
        "pets": r"\bpets?\b|\bdogs?\b|\bcats?\b",
        "smoking": r"\bsmok|\balcohol|\bdrinking\b|\bdrugs?\b|\bliquor",
        "parties_events": r"\bpart(y|ies)\b|\bevents?\b|\bgatherings?\b|loud music",
        "visitors": r"\bvisitors?\b|extra guests?|unregistered guests?",
        "quiet_hours": r"quiet hours|\bnoise\b|\bcurfew\b",
    }.items()
}


def topics_in(text: str) -> set[str]:
    """The policy topics a piece of free text talks about (possibly none)."""
    return {topic for topic, pattern in _TOPIC_PATTERNS.items() if pattern.search(text or "")}


# Said in a draft's review note when the document names a property that isn't
# the host's. policy_stance reads the same words to know such a policy is about
# somewhere else, so it never becomes a label or a recommendation filter.
UNMATCHED_NOTE_MARK = "isn't one of your properties"

TEXT_MAX = policy_documents.TEXT_MAX
TITLE_MAX = policy_documents.TITLE_MAX


class PolicyError(Exception):
    """A problem with the request, safe to show the host. `not_found` maps to 404."""

    def __init__(self, message: str, *, not_found: bool = False):
        super().__init__(message)
        self.not_found = not_found


# -- precedence ------------------------------------------------------------


def effective(active: list[Policy], property_id: uuid.UUID | None) -> list[Policy]:
    """What one property follows: host-wide policies, except where the property
    has its own of the same topic. "other" never replaces anything -- both the
    host-wide and the property's notes apply."""
    portfolio = [p for p in active if p.property_id is None]
    own = [p for p in active if property_id is not None and p.property_id == property_id]
    replaced = {p.topic for p in own if p.topic != OTHER}
    kept = [p for p in portfolio if p.topic == OTHER or p.topic not in replaced]
    return sorted(kept + own, key=lambda p: (p.topic, p.property_id is not None, str(p.id)))


@lru_cache(maxsize=4096)
def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


# Words that name a kind of place, not a particular one: a leading "The Villa"
# must not make every sentence about "the villa" a sentence about that property.
_GENERIC_NAME_WORDS = frozenset(
    "the a an villa villas house home stay stays cottage apartment suite room rooms pool luxury modern modernistic "
    "cosy cozy private beach farm homestay penthouse bhk spacez bungalow resort".split()
)


def _short_names(property_) -> tuple[str, ...]:
    return _names_from(property_.spoken_name, property_.display_name, property_.name)


@lru_cache(maxsize=4096)
def _names_from(spoken: str | None, display: str | None, name: str | None) -> tuple[str, ...]:
    """The ways a host would name this property in a sentence. Listings carry
    long titles ("Casa Blu 4BHK Spacez Villa with Outdoor Pool & Garden") while
    a host writes "Casa Blu", so the leading words of each name count: any
    prefix of two or more words (or a one-word name), as long as it holds at
    least one word that isn't generic ("villa", "the", ...)."""
    names = {n for n in (spoken, display, name) if n}
    names |= {re.split(r"[|\-\u2013,(]", n)[0] for n in list(names)}
    out: set[str] = set()
    for name in names:
        tokens = _norm_name(name).split()
        lengths = [1] if len(tokens) == 1 else range(2, len(tokens) + 1)
        for n in lengths:
            prefix = tokens[:n]
            if any(t not in _GENERIC_NAME_WORDS and len(t) >= 3 and not t[0].isdigit() for t in prefix) and len(" ".join(prefix)) >= 4:
                out.add(" ".join(prefix))
    return tuple(sorted(out))


def is_placeholder(policy: Policy) -> bool:
    """A host-wide policy that named a property the dashboard could not map to one
    of the host's: it is held back (spoken for nobody) until the host assigns it."""
    return policy.property_id is None and any(UNMATCHED_NOTE_MARK in note for note in (policy.review_notes or []))


def mentions_property(text: str, property_) -> bool:
    haystack = f" {_norm_name(text)} "
    return any(f" {name} " in haystack for name in _short_names(property_))


_ONLY_WORD = re.compile(r"\bonly\b", re.IGNORECASE)


def applicable(active: list[Policy], property_, siblings: list) -> list[Policy]:
    """What one property follows, with host-wide policies read for who they are
    really about. A host-wide policy that names some of the host's properties
    ("Pets are not allowed at Deccan Escape") is a statement about those, so the
    others do not carry it -- unless it says "only" ("Parties only at Casa Blu"),
    which is as much about everyone else. One that names none applies to all,
    except one a review note flagged as naming a place that isn't the host's and
    that still names none of their properties -- that is about somewhere else.
    Then the usual precedence (a property's own policy replaces the host-wide
    one of the same topic) via effective()."""
    kept = []
    for policy in active:
        if policy.property_id is None:
            if is_placeholder(policy):
                # Names a place the dashboard could not map to one of the host's
                # properties when it was read. Even if a property with that name
                # exists now, it stays out of every knowledge base until the host
                # assigns it (assign_policy): it is waiting for their say-so.
                continue
            named = [s for s in siblings if mentions_property(policy.text, s)]
            if named and property_.id not in {s.id for s in named} and not _ONLY_WORD.search(policy.text):
                continue
        kept.append(policy)
    return effective(kept, property_.id)


async def effective_for_property(db: AsyncSession, property_) -> list[Policy]:
    """The active policies that apply to this property (used when its
    knowledge-base facts are rebuilt). Scoped to the property's own host."""
    rows = (
        await db.scalars(
            select(Policy).where(Policy.user_id == property_.user_id, Policy.status == "active")
        )
    ).all()
    siblings = list((await db.scalars(select(Property).where(Property.user_id == property_.user_id))).all())
    return applicable(list(rows), property_, siblings)


async def resync(db: AsyncSession, user_id: uuid.UUID, property_ids: list[uuid.UUID] | None) -> int:
    """Rebuild the knowledge-base facts of the given properties (None = every
    property the host has). Returns how many were rebuilt."""
    from app.services.property.chunking import sync_property_facts

    stmt = select(Property).where(Property.user_id == user_id)
    if property_ids is not None:
        stmt = stmt.where(Property.id.in_(property_ids))
    properties = list((await db.scalars(stmt)).all())
    for property_ in properties:
        await sync_property_facts(db, property_)
    return len(properties)


async def sync_policy_scopes(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Rebuilds the knowledge base of every property whose set of applicable
    policies no longer matches what its stored facts were built from.

    Which host-wide policy applies to a property depends on ALL the host's
    property names (a policy that names "Soul Club" is about Soul Club only), so a
    property that is created, renamed, imported or deleted can change what its
    SIBLINGS should say. Rebuilding only the edited property left the others stale
    (a policy about one villa kept appearing in every villa's knowledge base).
    Only properties whose policy set actually differs are rebuilt. Returns how many."""
    from app.models.property_chunk import PropertyChunk
    from app.services.property.chunking import FACT_CHUNK_TYPE, sync_property_facts

    properties = list((await db.scalars(select(Property).where(Property.user_id == user_id))).all())
    if not properties:
        return 0
    active = list(
        (await db.scalars(select(Policy).where(Policy.user_id == user_id, Policy.status == "active"))).all()
    )
    rows = (
        await db.execute(
            select(PropertyChunk.property_id, PropertyChunk.source_ref).where(
                PropertyChunk.property_id.in_([p.id for p in properties]),
                PropertyChunk.chunk_type == FACT_CHUNK_TYPE,
                PropertyChunk.source_kind == "policy",
            )
        )
    ).all()
    stored: dict[uuid.UUID, set[str]] = defaultdict(set)
    for property_id, ref in rows:
        if ref:
            stored[property_id].add(ref)

    rebuilt = 0
    for property_ in properties:
        expected = {f"policy_{p.id}" for p in applicable(active, property_, properties)}
        if expected != stored.get(property_.id, set()):
            await sync_property_facts(db, property_)
            rebuilt += 1
    if rebuilt:
        logger.info("policy_scopes_resynced user_id=%s properties=%s", user_id, rebuilt)
    return rebuilt


async def sync_policy_scopes_detached(user_id: uuid.UUID) -> None:
    """sync_policy_scopes on its own session, for background.spawn after a
    property is created, renamed, imported or deleted. Never raises."""
    from app.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await sync_policy_scopes(db, user_id)
    except Exception:
        logger.exception("sync_policy_scopes failed user_id=%s", user_id)


# -- policies waiting for a property ---------------------------------------
#
# A policy the dashboard could not map to a property when it was read (the
# document named a villa that was not one of the host's yet) is held back as a
# PLACEHOLDER, and a draft that names a property waits for approval. Either way
# the host settles it from that property's pane: approve (it becomes the
# property's own policy), edit, or delete (not for this property). Which
# properties a shared one is already settled for is remembered as "handled:<id>"
# in review_notes (hidden from the API output).

HANDLED_PREFIX = "handled:"


def _handled_ids(policy: Policy) -> set[str]:
    return {n[len(HANDLED_PREFIX):] for n in (policy.review_notes or []) if n.startswith(HANDLED_PREFIX)}


def visible_notes(policy: Policy) -> list[str]:
    return [n for n in (policy.review_notes or []) if not n.startswith(HANDLED_PREFIX)]


def _names(policy: Policy, property_) -> bool:
    return mentions_property(f"{policy.title} {policy.text}", property_)


def _is_shared_proposal(policy: Policy) -> bool:
    """A host-wide draft or a placeholder: not live for anyone, offered to the
    properties it names one at a time."""
    return policy.property_id is None and (policy.status == "draft" or is_placeholder(policy))


def _already_settled(policies: list[Policy], policy: Policy, property_) -> bool:
    """The property already has its own live policy covering this."""
    for other in policies:
        if other.status != "active" or other.property_id != property_.id:
            continue
        if policy.topic != OTHER and other.topic == policy.topic:
            return True
        if policy.topic == OTHER and _norm(other.text) == _norm(policy.text):
            return True
    return False


def pending_for_property(policies: list[Policy], property_) -> list[Policy]:
    """The policies waiting on the host for this property."""
    out = []
    for p in policies:
        if p.status == "draft" and p.property_id == property_.id:
            out.append(p)
        elif _is_shared_proposal(p) and _names(p, property_) and str(property_.id) not in _handled_ids(p):
            if not _already_settled(policies, p, property_):
                out.append(p)
    return out


def unmapped_placeholders(policies: list[Policy], properties: list) -> list[Policy]:
    """Placeholders that name no property the host has at all."""
    return [
        p for p in policies if p.status == "active" and is_placeholder(p) and not any(_names(p, prop) for prop in properties)
    ]


async def _finish_shared_proposal(db: AsyncSession, user_id: uuid.UUID, policy: Policy, properties: list) -> None:
    """Once every property a shared proposal names has been settled, the
    original has done its job and goes away."""
    handled = _handled_ids(policy)
    remaining = [prop for prop in properties if _names(policy, prop) and str(prop.id) not in handled]
    if not remaining:
        await db.delete(policy)


async def assign_policy(db: AsyncSession, user: User, policy_id: uuid.UUID, property_id: uuid.UUID) -> Policy:
    """Approves a waiting policy for ONE property: it becomes that property's own
    live policy (replacing the property's current one for the same topic) and is
    spoken for that property only."""
    policy = await _get_owned(db, user.id, policy_id)
    property_ = await _owned_property(db, user.id, property_id)
    if policy.status == "draft" and policy.property_id == property_id:
        approved, _ = await approve(db, user, policy_id)
        return approved
    if not _is_shared_proposal(policy):
        raise PolicyError("That policy is not waiting for a property.")

    if policy.topic != OTHER:
        replaced = await _active_in_scope(db, user.id, property_id, policy.topic)
        if replaced is not None:
            await db.delete(replaced)
            await db.flush()
    scoped = Policy(
        user_id=user.id,
        property_id=property_id,
        source_document_id=policy.source_document_id,
        topic=policy.topic,
        title=policy.title,
        text=policy.text,
        status="active",
        source_excerpt=policy.source_excerpt,
        review_notes=[],
    )
    db.add(scoped)
    policy.review_notes = [*(policy.review_notes or []), f"{HANDLED_PREFIX}{property_.id}"]
    properties = list((await db.scalars(select(Property).where(Property.user_id == user.id))).all())
    await _finish_shared_proposal(db, user.id, policy, properties)
    await db.commit()
    await resync(db, user.id, [property_id])
    await db.refresh(scoped)
    return scoped


async def dismiss_policy(db: AsyncSession, user: User, policy_id: uuid.UUID, property_id: uuid.UUID) -> None:
    """"Not for this property": a property's own draft is deleted; a shared one is
    only dropped for this property and stays on offer for the others it names."""
    policy = await _get_owned(db, user.id, policy_id)
    await _owned_property(db, user.id, property_id)
    if policy.property_id == property_id and policy.status == "draft":
        await delete_policy(db, user, policy_id)
        return
    if not _is_shared_proposal(policy):
        raise PolicyError("That policy is not waiting for a property.")
    policy.review_notes = [*(policy.review_notes or []), f"{HANDLED_PREFIX}{property_id}"]
    properties = list((await db.scalars(select(Property).where(Property.user_id == user.id))).all())
    await _finish_shared_proposal(db, user.id, policy, properties)
    await db.commit()


# -- validation ------------------------------------------------------------


def _clean_text(value: str | None) -> str:
    text = re.sub(r"[ \t]+", " ", (value or "").strip())
    if not text:
        raise PolicyError("Write the policy first.")
    if len(text) > TEXT_MAX:
        raise PolicyError(f"Keep a policy under {TEXT_MAX} characters.")
    return text


def _clean_title(value: str | None, topic: str) -> str:
    title = " ".join((value or "").split())[:TITLE_MAX]
    return title or TOPIC_LABELS[topic]


def _check_topic(topic: str) -> str:
    topic = (topic or "").strip().lower()
    if topic not in TOPIC_LABELS:
        raise PolicyError("Unknown policy topic.")
    return topic


async def _owned_property(db: AsyncSession, user_id: uuid.UUID, property_id: uuid.UUID) -> Property:
    property_ = await db.scalar(select(Property).where(Property.id == property_id, Property.user_id == user_id))
    if property_ is None:
        raise PolicyError("Property not found.", not_found=True)
    return property_


async def _get_owned(db: AsyncSession, user_id: uuid.UUID, policy_id: uuid.UUID) -> Policy:
    policy = await db.scalar(select(Policy).where(Policy.id == policy_id, Policy.user_id == user_id))
    if policy is None:
        raise PolicyError("Policy not found.", not_found=True)
    return policy


async def _active_in_scope(
    db: AsyncSession, user_id: uuid.UUID, property_id: uuid.UUID | None, topic: str, *, exclude: uuid.UUID | None = None
) -> Policy | None:
    if topic == OTHER:
        return None
    stmt = select(Policy).where(
        Policy.user_id == user_id,
        Policy.status == "active",
        Policy.topic == topic,
        Policy.property_id.is_(None) if property_id is None else Policy.property_id == property_id,
    )
    if exclude is not None:
        stmt = stmt.where(Policy.id != exclude)
    return await db.scalar(stmt)


def _sync_scope(policy: Policy) -> list[uuid.UUID] | None:
    """Which properties' facts a change to this policy touches."""
    return None if policy.property_id is None else [policy.property_id]


# -- writes ----------------------------------------------------------------


async def create_policy(
    db: AsyncSession, user: User, *, topic: str, text: str, title: str | None = None, property_id: uuid.UUID | None = None
) -> Policy:
    """A policy the host wrote themselves: live immediately."""
    topic = _check_topic(topic)
    text = _clean_text(text)
    if property_id is not None:
        await _owned_property(db, user.id, property_id)
    if await _active_in_scope(db, user.id, property_id, topic) is not None:
        raise PolicyError("There is already a policy for this topic here. Edit it instead.")
    policy = Policy(
        user_id=user.id,
        property_id=property_id,
        topic=topic,
        title=_clean_title(title, topic),
        text=text,
        status="active",
        review_notes=[],
    )
    db.add(policy)
    await db.commit()
    await resync(db, user.id, _sync_scope(policy))
    await db.refresh(policy)  # server-generated timestamps, readable outside a greenlet
    return policy


async def create_policies(
    db: AsyncSession,
    user: User,
    *,
    topic: str,
    text: str,
    title: str | None = None,
    property_ids: list[uuid.UUID] | None = None,
) -> list[Policy]:
    """A policy the host wrote, live at once, for the whole portfolio (no
    property_ids) or for each of the chosen properties. All or nothing: if any
    target already has a policy for this topic, nothing is created and the host
    is told which. The knowledge base is re-synced once, for what changed."""
    topic = _check_topic(topic)
    text = _clean_text(text)
    targets: list[uuid.UUID | None] = list(dict.fromkeys(property_ids)) if property_ids else [None]
    names: dict[uuid.UUID, str] = {}
    for property_id in targets:
        if property_id is not None:
            prop = await _owned_property(db, user.id, property_id)
            names[property_id] = prop.display_name or prop.name
    if topic != OTHER:
        clashes = [
            names.get(pid, "your whole portfolio")
            for pid in targets
            if await _active_in_scope(db, user.id, pid, topic) is not None
        ]
        if clashes:
            raise PolicyError(
                f"{', '.join(clashes)} already ha{'s' if len(clashes) == 1 else 've'} a policy for {TOPIC_LABELS[topic]}. Edit that one instead."
            )
    created = [
        Policy(user_id=user.id, property_id=pid, topic=topic, title=_clean_title(title, topic), text=text, status="active", review_notes=[])
        for pid in targets
    ]
    db.add_all(created)
    await db.commit()
    await resync(db, user.id, None if None in targets else [pid for pid in targets if pid is not None])
    for policy in created:
        await db.refresh(policy)
    return created


async def update_policy(
    db: AsyncSession,
    user: User,
    policy_id: uuid.UUID,
    *,
    text: str | None = None,
    title: str | None = None,
    topic: str | None = None,
) -> Policy:
    policy = await _get_owned(db, user.id, policy_id)
    new_topic = _check_topic(topic) if topic is not None else policy.topic
    if policy.status == "active" and new_topic != policy.topic:
        if await _active_in_scope(db, user.id, policy.property_id, new_topic, exclude=policy.id) is not None:
            raise PolicyError("There is already a policy for that topic here.")
    if text is not None:
        policy.text = _clean_text(text)
    old_topic = policy.topic
    policy.topic = new_topic
    if title is not None:
        policy.title = _clean_title(title, new_topic)
    elif new_topic != old_topic and policy.title == TOPIC_LABELS.get(old_topic):
        policy.title = TOPIC_LABELS[new_topic]  # the title was just the old topic's name: follow the move
    # (a title the host wrote themselves is kept when the policy moves)
    await db.commit()
    if policy.status == "active":
        await resync(db, user.id, _sync_scope(policy))
    await db.refresh(policy)
    return policy


async def delete_policy(db: AsyncSession, user: User, policy_id: uuid.UUID) -> None:
    policy = await _get_owned(db, user.id, policy_id)
    was_live = policy.status == "active"
    scope = _sync_scope(policy)
    await db.delete(policy)
    await db.commit()
    if was_live:
        await resync(db, user.id, scope)


async def approve(
    db: AsyncSession,
    user: User,
    policy_id: uuid.UUID,
    *,
    property_ids: list[uuid.UUID] | None = None,
    all_properties: bool = False,
) -> tuple[Policy, bool]:
    """Makes a draft live. If a target scope already has an active policy for
    the topic, the draft replaces it (returned flag = True).

    By default the draft keeps the scope the document gave it. The host can
    override that while reviewing: `all_properties` makes it a host-wide
    policy, `property_ids` makes it each chosen property's own (the draft
    becomes the first, the rest are copies). Returns the draft itself."""
    policy = await _get_owned(db, user.id, policy_id)
    if policy.status == "active":
        return policy, False

    targets: list[uuid.UUID | None]
    if all_properties:
        targets = [None]
    elif property_ids:
        targets = list(dict.fromkeys(property_ids))
        for property_id in targets:
            await _owned_property(db, user.id, property_id)
    else:
        targets = [policy.property_id]

    replaced_any = False
    for index, property_id in enumerate(targets):
        replaced = await _active_in_scope(db, user.id, property_id, policy.topic)
        if replaced is not None:
            await db.delete(replaced)
            await db.flush()
            replaced_any = True
        if index == 0:
            policy.property_id = property_id
            policy.status = "active"
        else:
            db.add(
                Policy(
                    user_id=user.id,
                    property_id=property_id,
                    source_document_id=policy.source_document_id,
                    topic=policy.topic,
                    title=policy.title,
                    text=policy.text,
                    status="active",
                    source_excerpt=policy.source_excerpt,
                    review_notes=[],
                )
            )
        await db.flush()
    await db.commit()
    await resync(db, user.id, None if None in targets else [pid for pid in targets if pid is not None])
    await db.refresh(policy)
    return policy, replaced_any


async def approve_all(db: AsyncSession, user: User, document_id: uuid.UUID | None = None) -> int:
    """Approves every draft (or those from one document). Within the batch the
    later draft wins when two cover the same topic and scope."""
    stmt = select(Policy).where(Policy.user_id == user.id, Policy.status == "draft").order_by(Policy.created_at)
    if document_id is not None:
        stmt = stmt.where(Policy.source_document_id == document_id)
    drafts = list((await db.scalars(stmt)).all())
    touched_all = False
    touched: set[uuid.UUID] = set()
    for draft in drafts:
        replaced = await _active_in_scope(db, user.id, draft.property_id, draft.topic)
        if replaced is not None:
            await db.delete(replaced)
            await db.flush()
        draft.status = "active"
        await db.flush()
        if draft.property_id is None:
            touched_all = True
        else:
            touched.add(draft.property_id)
    await db.commit()
    if drafts:
        await resync(db, user.id, None if touched_all else sorted(touched))
    return len(drafts)


# -- documents -------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def match_properties(names: list[str], properties: list[Property]) -> tuple[list[Property], list[str]]:
    """Maps property names a document used to the host's properties: exact
    (case/punctuation-insensitive) on name, display name or spoken name, or one
    containing the other when the shorter is at least 4 characters."""
    matched: dict[uuid.UUID, Property] = {}
    unmatched: list[str] = []
    for name in names:
        wanted = _norm(name)
        hits = []
        for prop in properties:
            labels = [_norm(x) for x in (prop.name, prop.display_name, prop.spoken_name) if x]
            if any(wanted == label or (min(len(wanted), len(label)) >= 4 and (wanted in label or label in wanted)) for label in labels):
                hits.append(prop)
        if hits:
            for prop in hits:
                matched[prop.id] = prop
        else:
            unmatched.append(name)
    return list(matched.values()), unmatched


async def ingest_document(
    db: AsyncSession, user: User, *, filename: str, content_type: str | None, data: bytes
) -> tuple[PolicyDocument, list[Policy], int]:
    """Reads an uploaded file into DRAFT policies. Returns (document, drafts,
    how many were skipped because the same policy is already live or waiting).
    Nothing reaches the agent until the host approves."""
    properties = list((await db.scalars(select(Property).where(Property.user_id == user.id))).all())
    result, source_text = await policy_documents.read_document(
        filename,
        data,
        topics=TOPICS,
        property_names=[p.display_name or p.name for p in properties],
    )

    existing = list(
        (await db.scalars(select(Policy).where(Policy.user_id == user.id, Policy.status.in_(("active", "draft"))))).all()
    )
    have = {(p.property_id, p.topic, _norm(p.text)) for p in existing}
    haystack = _norm(source_text) if source_text else None

    document = PolicyDocument(
        user_id=user.id, filename=filename[:255], content_type=(content_type or "")[:120] or None,
        size_bytes=len(data), truncated=result.truncated,
    )
    db.add(document)
    await db.flush()

    drafts: list[Policy] = []
    skipped = 0
    for item in result.items:
        targets, unmatched = match_properties(item.properties, properties) if item.properties else ([], [])
        if not targets and not item.properties:
            # The reader named no property, but the text may still be about one
            # ("Parties are not allowed at Soul Club"): map it by name so it is
            # offered to that property (as a draft, for the host to approve)
            # instead of reaching every property as a host-wide rule.
            targets = [prop for prop in properties if mentions_property(f"{item.title} {item.text}", prop)]
        notes: list[str] = []
        if result.from_image:
            notes.append("Read from an image -- check this carefully against the original.")
        elif item.excerpt and haystack is not None and _norm(item.excerpt) not in haystack:
            notes.append("Couldn't find this exact wording in the document -- check it against the original.")
        if unmatched:
            notes.append("The document mentions " + ", ".join(unmatched) + f", which {UNMATCHED_NOTE_MARK}.")
        for property_id in ([p.id for p in targets] or [None]):
            if (property_id, item.topic, _norm(item.text)) in have:
                skipped += 1
                continue
            have.add((property_id, item.topic, _norm(item.text)))
            draft = Policy(
                user_id=user.id,
                property_id=property_id,
                source_document_id=document.id,
                topic=item.topic,
                title=_clean_title(item.title, item.topic),
                text=item.text,
                status="draft",
                source_excerpt=item.excerpt,
                review_notes=notes,
            )
            db.add(draft)
            drafts.append(draft)
    document.item_count = len(drafts)
    await db.commit()
    return document, drafts, skipped


# -- reading ---------------------------------------------------------------


def _row_state(items: list[Policy]) -> str:
    has_portfolio = any(p.property_id is None for p in items)
    has_property = any(p.property_id is not None for p in items)
    if has_portfolio and has_property:
        return "some_differ"
    if has_portfolio:
        return "everyone"
    return "property_only" if has_property else "unset"


def policy_out(p: Policy, names: dict, filenames: dict) -> dict:
    """A policy as the API shows it (names/filenames are id -> label maps)."""
    return {
        "id": p.id,
        "topic": p.topic,
        "title": p.title,
        "text": p.text,
        "status": p.status,
        "property_id": p.property_id,
        "property_name": names.get(p.property_id) if p.property_id else None,
        "source_document_id": p.source_document_id,
        "source_filename": filenames.get(p.source_document_id),
        "source_excerpt": p.source_excerpt,
        "review_notes": visible_notes(p),
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


async def label_maps(db: AsyncSession, user_id: uuid.UUID) -> tuple[dict, dict]:
    """(property id -> display name, document id -> filename) for policy_out."""
    props = (await db.execute(select(Property.id, Property.display_name, Property.name).where(Property.user_id == user_id))).all()
    docs = (await db.execute(select(PolicyDocument.id, PolicyDocument.filename).where(PolicyDocument.user_id == user_id))).all()
    return {pid: (display or name) for pid, display, name in props}, {did: fn for did, fn in docs}


async def overview(db: AsyncSession, user: User) -> dict:
    """Everything the Policies tab shows: a row per topic with its live
    policies (host-wide first, then property overrides), the drafts waiting for
    review, the uploaded documents and the host's properties."""
    properties = list((await db.scalars(select(Property).where(Property.user_id == user.id).order_by(Property.name))).all())
    names = {p.id: (p.display_name or p.name) for p in properties}
    policies = list(
        (await db.scalars(select(Policy).where(Policy.user_id == user.id).order_by(Policy.created_at))).all()
    )
    documents = list(
        (await db.scalars(select(PolicyDocument).where(PolicyDocument.user_id == user.id).order_by(PolicyDocument.created_at.desc()).limit(20))).all()
    )
    filenames = {d.id: d.filename for d in documents}

    live: dict[str, list[Policy]] = defaultdict(list)
    for p in policies:
        if p.status == "active":
            live[p.topic].append(p)
    rows = []
    for key, label, hint in TOPICS:
        items = sorted(live.get(key, []), key=lambda p: (p.property_id is not None, names.get(p.property_id, ""), p.created_at))
        rows.append(
            {
                "topic": key,
                "label": label,
                "hint": hint,
                "state": _row_state(items),
                "override_count": sum(1 for p in items if p.property_id is not None),
                "items": [policy_out(p, names, filenames) for p in items],
            }
        )
    from app.services import policy_stance

    active = [p for p in policies if p.status == "active"]
    labels = {
        prop.id: [{"key": x.key, "label": x.label} for x in policy_stance.labels_for_property(active, prop, properties)]
        for prop in properties
    }
    follows = {prop.id: [p.id for p in applicable(active, prop, properties)] for prop in properties}
    pending = {prop.id: [p.id for p in pending_for_property(policies, prop)] for prop in properties}
    return {
        "rows": rows,
        "drafts": [policy_out(p, names, filenames) for p in policies if p.status == "draft"],
        "documents": [
            {"id": d.id, "filename": d.filename, "item_count": d.item_count, "truncated": d.truncated, "created_at": d.created_at}
            for d in documents
        ],
        "properties": [
            {
                "id": p.id,
                "name": names[p.id],
                "city": p.city,
                "labels": labels[p.id],
                "policy_ids": follows[p.id],
                "pending_policy_ids": pending[p.id],
            }
            for p in properties
        ],
        "unmapped_policy_ids": [p.id for p in unmapped_placeholders(policies, properties)],
        "topics": [{"key": k, "label": label, "hint": hint} for k, label, hint in TOPICS],
    }
