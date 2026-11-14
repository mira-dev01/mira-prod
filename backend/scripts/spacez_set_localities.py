"""SPACEZ ONLY. Fills properties.locality (and locality_key) for a host's properties.

Spacez titles put the area in the name ("4BHK Jugglers Rest Spacez Villa in Indiranagar") and nowhere else, so the
location search could not find it (call 56947e99, 2026-11-13). Most titles name no area at all; those stay without
a locality and are listed, never guessed. Two sources, in this order:

  1. scripts/data/spacez_localities.json -- hand-checked overrides, matched by name prefix.
  2. The title itself, only for a short, clean "in <Area>" ending: one or two words, none of them a generic
     word (villa, apartment, ...), and not the city's own name.

A property that already has a locality is left alone (the host may have set it) unless --overwrite.

--with-points also puts each property that has a locality on the map at that neighbourhood (from the gazetteer,
scripts/load_gazetteer.py), which is what lets "near Indiranagar" give real distances. It needs the gazetteer
loaded for the host's states; a property whose area or city it cannot place unambiguously is listed, not guessed.
A point the host set themselves is never replaced.

Dry-run by default. Run (from backend/):
    python -m scripts.spacez_set_localities --user-email hello@spacez.dev
    python -m scripts.spacez_set_localities --user-email hello@spacez.dev --apply --with-points
Needs the c7f1a3d9e5b2 migration applied first (alembic upgrade head).
"""

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from app.database import AsyncSessionLocal, engine
from app.models.property import Property
from app.models.user import User
from app.services.geo.property_location import guess_point_from_locality
from app.services.geo.text import normalize
from app.services.property.locality import clean_locality, locality_key, set_locality

DEFAULT_OVERRIDES = Path(__file__).parent / "data" / "spacez_localities.json"

# "... in Indiranagar", "... w/ Patio In Sarjapur": the words after the LAST " in ", to the end of the title.
_IN_AREA = re.compile(r"\b[Ii]n\s+([A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*)*)\s*$")
_MAX_AREA_WORDS = 2
# Words that mean the capture is part of a property description, not an area.
_GENERIC_WORDS = {
    "villa", "villas", "apartment", "apartments", "penthouse", "pent", "house", "home", "stay", "stays", "room",
    "rooms", "terrace", "garden", "pool", "theatre", "theater", "cinema", "court", "manor", "estate", "residency",
    "resort", "cottage", "bungalow", "farm", "luxury", "private", "spacez", "bhk", "heart", "centre", "center",
    "the", "a", "an", "your", "our", "city", "town",
}


def parse_locality(name: str, city: str | None) -> str | None:
    """The area a title states, or None. Conservative on purpose: a wrong area is worse than none."""
    match = _IN_AREA.search(name or "")
    if not match:
        return None
    area = clean_locality(match.group(1))
    if not area:
        return None
    words = area.split()
    if len(words) > _MAX_AREA_WORDS or any(normalize(w) in _GENERIC_WORDS for w in words):
        return None
    if locality_key(area) == locality_key(city):
        return None  # "... villa in Hyderabad": that is the city, which the property already has
    return area


def load_overrides(path: Path) -> list[dict]:
    return json.loads(path.read_text()).get("overrides", [])


def override_for(name: str, overrides: list[dict]) -> tuple[str | None, int]:
    """(locality, how many overrides matched this name). More than one is an error the caller reports."""
    lowered = name.lower()
    hits = [o for o in overrides if lowered.startswith(o["name"].lower())]
    return (hits[0]["locality"] if len(hits) == 1 else None), len(hits)


async def _report_points(db, properties: list[Property]) -> None:
    """Place each property that has a locality on the map; list the ones that could not be placed.
    Changes are only in this session: main() commits them with --apply and rolls back otherwise."""
    placed, unplaced = 0, []
    for prop in properties:
        if not prop.locality:
            continue
        ok = await guess_point_from_locality(db, prop)
        placed += ok
        if not ok and prop.latitude is None:
            unplaced.append(prop)
    print(f"\nMap points set: {placed}")
    for prop in unplaced:
        print(f"  - could not place {prop.locality}, {prop.city}: no single match in the gazetteer ({prop.name[:40]})")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--file", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--overwrite", action="store_true", help="replace a locality that is already set")
    parser.add_argument("--with-points", action="store_true", help="also set each located property's map point from the gazetteer")
    parser.add_argument("--apply", action="store_true", help="write the changes (default: preview only)")
    args = parser.parse_args()

    overrides = load_overrides(args.file)
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == args.user_email))
        if user is None:
            print(f"No host with email {args.user_email}")
            return 2
        properties = list(
            (await db.scalars(select(Property).where(Property.user_id == user.id).order_by(Property.city, Property.name))).all()
        )

        problems = 0
        unresolved: list[Property] = []
        by_city: Counter[str] = Counter()
        located: Counter[str] = Counter()
        print(f"{'name':58s} | {'city':12s} | locality (source)")
        for prop in properties:
            city = prop.city or "(no city)"
            by_city[city] += 1
            chosen, source = None, ""
            fixed, matched = override_for(prop.name, overrides)
            if matched > 1:
                print(f"  !! {prop.name}: {matched} overrides match (need exactly 1)")
                problems += 1
                continue
            if fixed:
                chosen, source = fixed, "override"
            elif parse_locality(prop.name, prop.city):
                chosen, source = parse_locality(prop.name, prop.city), "title"
            if prop.locality and not args.overwrite:
                chosen, source = prop.locality, "already set"
            print(f"{prop.name[:58]:58s} | {city[:12]:12s} | {chosen or '-'} {('(' + source + ')') if source else ''}")
            if chosen:
                located[city] += 1
                if (args.apply or args.with_points) and source != "already set":
                    set_locality(prop, chosen)  # in memory only unless --apply: a dry run rolls back below
            else:
                unresolved.append(prop)

        print("\nCoverage (city: with locality / properties):")
        for city, total in sorted(by_city.items()):
            print(f"  {city:20s} {located[city]}/{total}")
        print(f"\nStill without a locality ({len(unresolved)}): add the real ones to {args.file.name}")
        for prop in unresolved:
            print(f"  - {prop.name} ({prop.city})")

        if args.with_points:
            await _report_points(db, properties)

        if problems:
            print("Fix the entries above first; nothing written.")
            await db.rollback()
            return 1
        if args.apply:
            await db.commit()
            print("Saved.")
        else:
            await db.rollback()
            print("Preview only; add --apply to write.")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
