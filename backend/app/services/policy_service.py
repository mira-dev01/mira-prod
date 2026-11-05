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


async def effective_for_property(db: AsyncSession, property_) -> list[Policy]:
    """The active policies that apply to this property (used when its
    knowledge-base facts are rebuilt). Scoped to the property's own host."""
    rows = (
        await db.scalars(
            select(Policy).where(
                Policy.user_id == property_.user_id,
                Policy.status == "active",
                (Policy.property_id.is_(None)) | (Policy.property_id == property_.id),
            )
        )
    ).all()
    return effective(list(rows), property_.id)


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


async def approve(db: AsyncSession, user: User, policy_id: uuid.UUID) -> tuple[Policy, bool]:
    """Makes a draft live. If this scope already has an active policy for the
    topic, the draft replaces it (returned flag = True)."""
    policy = await _get_owned(db, user.id, policy_id)
    if policy.status == "active":
        return policy, False
    replaced = await _active_in_scope(db, user.id, policy.property_id, policy.topic)
    if replaced is not None:
        await db.delete(replaced)
        await db.flush()
    policy.status = "active"
    await db.commit()
    await resync(db, user.id, _sync_scope(policy))
    await db.refresh(policy)
    return policy, replaced is not None


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
        notes: list[str] = []
        if result.from_image:
            notes.append("Read from an image -- check this carefully against the original.")
        elif item.excerpt and haystack is not None and _norm(item.excerpt) not in haystack:
            notes.append("Couldn't find this exact wording in the document -- check it against the original.")
        if unmatched:
            notes.append("The document mentions " + ", ".join(unmatched) + ", which isn't one of your properties.")
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
        "review_notes": list(p.review_notes or []),
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
    return {
        "rows": rows,
        "drafts": [policy_out(p, names, filenames) for p in policies if p.status == "draft"],
        "documents": [
            {"id": d.id, "filename": d.filename, "item_count": d.item_count, "truncated": d.truncated, "created_at": d.created_at}
            for d in documents
        ],
        "properties": [{"id": p.id, "name": names[p.id], "city": p.city} for p in properties],
        "topics": [{"key": k, "label": label, "hint": hint} for k, label, hint in TOPICS],
    }
