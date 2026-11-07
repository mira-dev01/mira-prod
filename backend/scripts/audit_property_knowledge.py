"""Checks that every property of one host carries the right knowledge: its own
fields, its FAQs (imported ones included) and the approved policies that apply
to it -- and that no property carries another's. Read-only unless --fix.

Run (from backend/):
    python -m scripts.audit_property_knowledge --email host@example.com
    python -m scripts.audit_property_knowledge --email host@example.com --fix   # re-sync properties found stale

Exit code 1 if any property has a problem (after --fix, if given: re-checked).
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import policy_audit  # noqa: E402


def _print(audits) -> None:
    for audit in audits:
        print(f"\n{'OK ' if audit.ok else 'BAD'} {audit.name}")
        if audit.facts_by_kind:
            print("    facts: " + ", ".join(f"{k}={v}" for k, v in sorted(audit.facts_by_kind.items())))
        if audit.labels:
            print("    labels: " + " | ".join(audit.labels))
        for line in audit.policies:
            print(f"    policy: {line[:140]}")
        for line in audit.problems:
            print(f"    PROBLEM: {line}")
        for line in dict.fromkeys(audit.notes):
            print(f"    note: {line}")


async def main(email: str, fix: bool) -> int:
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == email))
        if user is None:
            print(f"No host with email {email}")
            return 2
        audits = await policy_audit.audit_host(db, user.id)
        if fix and any(not a.ok for a in audits):
            fixed = await policy_audit.fix_stale(db, user.id, audits)
            print(f"Re-synced {fixed} propert{'y' if fixed == 1 else 'ies'}; checking again.")
            audits = await policy_audit.audit_host(db, user.id)
        _print(audits)
        bad = [a for a in audits if not a.ok]
        print(f"\n{len(audits)} propert{'y' if len(audits) == 1 else 'ies'} checked, {len(bad)} with problems.")
        return 1 if bad else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--fix", action="store_true", help="re-sync the knowledge base of properties found stale")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.email, args.fix)))
