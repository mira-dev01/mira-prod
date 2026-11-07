"""Read-only check that each of a host's properties carries exactly the
knowledge it should: its own fields, its verified FAQs (imported ones included)
and the approved policies that apply to *it* -- and nothing else.

For every property it rebuilds the knowledge base the way a sync would
(chunking.build_fact_chunks over the property, its verified FAQ rows and its
effective policies) and compares that with what is stored:

- stale: a fact that should be there is missing, or one that should not be is.
- leak: a stored policy fact that is not one of this property's effective
  policies (another property's override, another host's policy, a draft).
- coarse chunks (overview/amenities/location/house rules) match the property's fields.

Differences that are by design are reported as notes, not problems: an imported
FAQ kept in the FAQ list but left out of the knowledge base because an approved
policy covers its topic, drafts still waiting for approval, and policies a
review note flagged as naming a property that isn't the host's.

Writes nothing. `fix_stale` (used by scripts/audit_property_knowledge.py --fix)
re-syncs only the properties that were found stale.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.faq_entry import FaqEntry
from app.models.policy import Policy
from app.models.property import Property
from app.models.property_chunk import PropertyChunk
from app.services import policy_service, policy_stance
from app.services.property import chunking


@dataclass
class PropertyAudit:
    property_id: uuid.UUID
    name: str
    facts_by_kind: dict[str, int] = field(default_factory=dict)
    policies: list[str] = field(default_factory=list)  # "<topic> (<scope>): <text>"
    labels: list[str] = field(default_factory=list)
    policy_ids: list[uuid.UUID] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _scope(policy: Policy) -> str:
    return "this property only" if policy.property_id else "all properties"


async def audit_property(db: AsyncSession, property_: Property, siblings: list[Property], active: list[Policy]) -> PropertyAudit:
    result = PropertyAudit(property_id=property_.id, name=property_.display_name or property_.name)
    effective = policy_service.applicable(active, property_, siblings)
    result.policy_ids = [p.id for p in effective]
    result.policies = [f"{p.topic} ({_scope(p)}): {p.text}" for p in effective]
    result.labels = [x.label for x in policy_stance.labels_for_property(active, property_, siblings)]

    faqs = list(
        (
            await db.scalars(
                select(FaqEntry).where(FaqEntry.property_id == property_.id, FaqEntry.status == "verified")
            )
        ).all()
    )
    expected = chunking.build_fact_chunks(property_, faqs, effective)
    expected_texts = {d.text for d in expected}
    expected_policy_refs = {d.source_ref for d in expected if d.source_kind == "policy"}

    chunks = list((await db.scalars(select(PropertyChunk).where(PropertyChunk.property_id == property_.id))).all())
    facts = [c for c in chunks if c.chunk_type == chunking.FACT_CHUNK_TYPE]
    stored_texts = {c.text for c in facts}
    result.facts_by_kind = dict(Counter(c.source_kind or "unknown" for c in facts))

    missing = sorted(expected_texts - stored_texts)
    extra = sorted(stored_texts - expected_texts)
    if missing:
        result.problems.append(f"stale: {len(missing)} fact(s) should be on file but are not, e.g. {missing[0][:90]!r}")
    if extra:
        result.problems.append(f"stale: {len(extra)} stored fact(s) should not be there, e.g. {extra[0][:90]!r}")

    stored_policy_refs = {c.source_ref for c in facts if c.source_kind == "policy"}
    leaked = stored_policy_refs - expected_policy_refs
    if leaked:
        result.problems.append(f"leak: {len(leaked)} policy fact(s) stored here that do not apply to this property")
    for policy in effective:
        if f"policy_{policy.id}" not in stored_policy_refs:
            result.problems.append(f"missing: approved {policy.topic} policy is not in this property's knowledge base")

    # Coarse chunks vs the property's own fields.
    wanted = chunking.build_property_chunks(property_)
    have = {c.chunk_type: c.text for c in chunks if c.chunk_type != chunking.FACT_CHUNK_TYPE}
    for chunk_type, text in wanted.items():
        if have.get(chunk_type) != text:
            result.problems.append(f"stale: {chunk_type} chunk does not match the property's current fields")
    for chunk_type in have.keys() - wanted.keys():
        result.problems.append(f"stale: {chunk_type} chunk exists but the property has nothing for it")

    # By design, not problems.
    kept_faq_ids = {d.source_ref for d in expected if d.source_kind == "faq_entry"}
    hidden = [f for f in faqs if str(f.id) not in kept_faq_ids and getattr(f, "category", None) != "booking_scope"]
    if hidden:
        result.notes.append(
            f"{len(hidden)} imported FAQ(s) kept in the FAQ list but not spoken, because an approved policy covers their topic"
        )
    return result


async def audit_host(db: AsyncSession, host_user_id: uuid.UUID) -> list[PropertyAudit]:
    properties = list(
        (await db.scalars(select(Property).where(Property.user_id == host_user_id).order_by(Property.name))).all()
    )
    policies = list((await db.scalars(select(Policy).where(Policy.user_id == host_user_id))).all())
    active = [p for p in policies if p.status == "active"]
    audits = [await audit_property(db, prop, properties, active) for prop in properties]

    drafts = [p for p in policies if p.status == "draft"]
    if drafts and audits:
        waiting = f"{len(drafts)} policy draft(s) waiting for approval (not spoken yet)"
        for audit in audits:
            audit.notes.append(waiting)
    used = {pid for audit in audits for pid in audit.policy_ids}
    for policy in active:
        if policy.id not in used and policy.property_id is None:
            for audit in audits:
                audit.notes.append(
                    f"approved {policy.topic} policy ({policy.text[:50]!r}) is about a place that isn't one of your properties, so no property uses it"
                )
    return audits


async def fix_stale(db: AsyncSession, host_user_id: uuid.UUID, audits: list[PropertyAudit]) -> int:
    """Re-syncs the knowledge base of the properties the audit found problems on."""
    fixed = 0
    for audit in audits:
        if audit.ok:
            continue
        prop = await db.get(Property, audit.property_id)
        if prop is None or prop.user_id != host_user_id:
            continue
        await chunking.sync_property_chunks(db, prop)
        await chunking.sync_property_facts(db, prop)
        fixed += 1
    return fixed
