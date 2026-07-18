"""
One-off backfill: transliterate existing Devanagari-script guest names to
Latin/English script, on Lead.guest_name and GuestProfile.name.

Why: before the fix in app/voice/tools.py (update_lead's guest_name arg now
explicitly instructs the model to write names in Latin script), a guest
speaking Hindi mid-call could get their name captured in Devanagari. Two
calls from the same guest -- one in Hindi, one in English -- then produced
two non-matching guest_name strings, breaking guest-uniqueness/dedup in the
guest memory system. See restructure.md Phase 6.

This is a silent, host-data-mutating script (per explicit product decision:
auto-transliterate, no host review gate) -- treated accordingly:
  - Dry-run by default. Pass --apply to actually write.
  - Logs a before/after line for every row it changes (or would change),
    so there's a paper trail if a transliteration is ever questioned.
  - Only a small, hand-verified mapping is used (see NAME_MAP below) --
    deliberately NOT a general transliteration library. At the time this
    script was written, the entire dataset was 5 rows across both tables;
    for a set this small, a human-verified mapping is more accurate than
    any generic Devanagari->Latin romanizer, and avoids pulling in a heavy
    dependency (indic-transliteration and friends) for a one-off script.
    If a future run finds a Devanagari name NOT in NAME_MAP, it is reported
    as SKIPPED (unmapped) rather than guessed at -- add it to NAME_MAP by
    hand (confirm with the host if the correct spelling is unclear) and
    re-run.

Usage (run from the backend/ directory):
    DATABASE_URL=<db-url> python backfill_guest_names_to_english.py          # dry run
    DATABASE_URL=<db-url> python backfill_guest_names_to_english.py --apply  # writes

Or if .env already points at the right database:
    python backfill_guest_names_to_english.py
    python backfill_guest_names_to_english.py --apply
"""

import asyncio
import re
import sys

sys.path.insert(0, ".")

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.guest_profile import GuestProfile
from app.models.lead import Lead

DEVANAGARI_PATTERN = re.compile(r"[ऀ-ॿ]")

# Hand-verified as of 2026-07-16 against the then-current dataset (5 rows:
# 5 in Lead.guest_name, 0 in GuestProfile.name). Extend this by hand --
# never auto-generate an entry here.
NAME_MAP: dict[str, str] = {
    "आभ्या": "Aabhya",
    "अभय त्रिवेदी": "Abhay Trivedi",
    "सिद्धार्थ कटपालिया": "Siddharth Katpalia",
    "शगुन": "Shagun",
}


async def backfill(apply: bool) -> None:
    mode = "APPLY" if apply else "DRY RUN"
    print(f"=== Guest name English-normalization backfill ({mode}) ===\n")

    changed = 0
    skipped_unmapped: list[str] = []

    async with AsyncSessionLocal() as db:
        leads = (await db.scalars(select(Lead).where(Lead.guest_name.isnot(None)))).all()
        for lead in leads:
            if not DEVANAGARI_PATTERN.search(lead.guest_name):
                continue
            mapped = NAME_MAP.get(lead.guest_name)
            if mapped is None:
                skipped_unmapped.append(f"Lead {lead.id}: {lead.guest_name!r} (no mapping)")
                continue
            print(f"Lead {lead.id}: {lead.guest_name!r} -> {mapped!r}")
            changed += 1
            if apply:
                lead.guest_name = mapped

        guests = (await db.scalars(select(GuestProfile).where(GuestProfile.name.isnot(None)))).all()
        for guest in guests:
            if not DEVANAGARI_PATTERN.search(guest.name):
                continue
            mapped = NAME_MAP.get(guest.name)
            if mapped is None:
                skipped_unmapped.append(f"GuestProfile {guest.id}: {guest.name!r} (no mapping)")
                continue
            print(f"GuestProfile {guest.id}: {guest.name!r} -> {mapped!r}")
            changed += 1
            if apply:
                guest.name = mapped

        if apply and changed:
            await db.commit()

    print(f"\n{changed} row(s) {'updated' if apply else 'would be updated'}.")
    if skipped_unmapped:
        print(f"\n{len(skipped_unmapped)} row(s) SKIPPED (Devanagari name with no entry in NAME_MAP):")
        for line in skipped_unmapped:
            print(f"  - {line}")
        print("\nAdd these to NAME_MAP by hand (confirm spelling with the host if unclear) and re-run.")

    if not apply and changed:
        print("\nThis was a dry run -- no changes were written. Re-run with --apply to write them.")


if __name__ == "__main__":
    asyncio.run(backfill(apply="--apply" in sys.argv))
