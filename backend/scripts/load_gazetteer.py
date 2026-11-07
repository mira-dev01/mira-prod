"""Loads India's place names (GeoNames, CC BY 4.0) into the gazetteer tables
that location search reads (app/models/place.py). Safe to re-run: it replaces
the tables' contents in one transaction.

Not required for correctness: until this runs, place lookups find nothing and
recommend_properties falls back to its free-text location match, so deploying
the migration first regresses nothing.

Needs three files from https://download.geonames.org/export/dump/ -- IN.zip
(unzip to IN.txt), admin1CodesASCII.txt, admin2Codes.txt -- either already in
--dir, or fetched for you with --download (~18 MB). Attribution: this product
uses data from GeoNames (geonames.org).

Run (from backend/):
    python -m scripts.load_gazetteer --download --dir /tmp/geonames
    python -m scripts.load_gazetteer --dir /tmp/geonames                 # files already there
    python -m scripts.load_gazetteer --dir /tmp/geonames --admin1 11 39  # only Himachal + Uttarakhand
    python -m scripts.load_gazetteer --dir /tmp/geonames --dry-run       # count rows, write nothing

Only populated places (feature class P) are kept: villages, towns and cities.
--admin1 narrows to specific states by GeoNames' numeric code (IN.11 =
Himachal Pradesh) for a smaller footprint on a small database.
"""

import argparse
import asyncio
import csv
import sys
import urllib.request
import zipfile
from pathlib import Path

from sqlalchemy import text

from app.database import engine
from app.services.geo.text import normalize, skeleton

_BASE_URL = "https://download.geonames.org/export/dump/"
_MAX_ALIASES_PER_PLACE = 6
_NAME_COL, _ASCII_COL, _ALT_COL, _LAT_COL, _LNG_COL = 1, 2, 3, 4, 5
_CLASS_COL, _ADMIN1_COL, _ADMIN2_COL, _POP_COL = 6, 10, 11, 14


def _download(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("IN.zip", "admin1CodesASCII.txt", "admin2Codes.txt"):
        target = directory / name
        if not target.exists():
            print(f"downloading {name} ...")
            urllib.request.urlretrieve(_BASE_URL + name, target)
    if not (directory / "IN.txt").exists():
        with zipfile.ZipFile(directory / "IN.zip") as archive:
            archive.extract("IN.txt", directory)


def _admin_regions(directory: Path, only_admin1: set[str] | None) -> list[tuple[str, int, str]]:
    rows: list[tuple[str, int, str]] = []
    for filename, level in (("admin1CodesASCII.txt", 1), ("admin2Codes.txt", 2)):
        with open(directory / filename, encoding="utf-8") as handle:
            for code, name, *_ in csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
                if not code.startswith("IN."):
                    continue
                if only_admin1 and code.split(".")[1] not in only_admin1:
                    continue
                rows.append((code, level, name[:120]))
    return rows


def _places(directory: Path, only_admin1: set[str] | None):
    """-> (place rows, alias rows)."""
    places: list[tuple] = []
    aliases: list[tuple] = []
    with open(directory / "IN.txt", encoding="utf-8") as handle:
        for row in csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
            if row[_CLASS_COL] != "P":
                continue
            admin1 = row[_ADMIN1_COL]
            if only_admin1 and admin1 not in only_admin1:
                continue
            name_norm = normalize(row[_ASCII_COL] or row[_NAME_COL])
            if not name_norm:
                continue
            geoname_id = int(row[0])
            admin2 = row[_ADMIN2_COL]
            places.append(
                (
                    geoname_id,
                    (row[_ASCII_COL] or row[_NAME_COL])[:120],  # ASCII: spoken aloud by the voice
                    name_norm[:120],
                    skeleton(name_norm),
                    float(row[_LAT_COL]),
                    float(row[_LNG_COL]),
                    f"IN.{admin1}" if admin1 else None,
                    f"IN.{admin1}.{admin2}" if admin1 and admin2 else None,
                    int(row[_POP_COL] or 0),
                )
            )
            seen = {name_norm}
            for alt in row[_ALT_COL].split(","):
                if len(seen) > _MAX_ALIASES_PER_PLACE:
                    break
                alias = normalize(alt) if alt.isascii() else ""
                if alias and alias not in seen and len(alias) <= 120:
                    seen.add(alias)
                    aliases.append((geoname_id, alias, skeleton(alias)))
    return places, aliases


async def _load(places, aliases, regions) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE place_aliases, places, admin_regions"))
        raw = (await conn.get_raw_connection()).driver_connection  # asyncpg: COPY is far faster than INSERTs
        await raw.copy_records_to_table("admin_regions", records=regions, columns=["code", "level", "name"])
        await raw.copy_records_to_table(
            "places",
            records=places,
            columns=["geoname_id", "name", "name_norm", "name_key", "lat", "lng", "admin1_code", "admin2_code", "population"],
        )
        await raw.copy_records_to_table("place_aliases", records=aliases, columns=["place_id", "alias_norm", "alias_key"])
        await conn.execute(text("ANALYZE places"))
        await conn.execute(text("ANALYZE place_aliases"))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, required=True, help="folder holding (or to receive) the GeoNames files")
    parser.add_argument("--download", action="store_true", help="fetch the GeoNames files into --dir if missing")
    parser.add_argument("--admin1", nargs="*", help="only these GeoNames state codes, e.g. 11 39")
    parser.add_argument("--dry-run", action="store_true", help="count rows, write nothing")
    args = parser.parse_args()

    if args.download:
        _download(args.dir)
    missing = [n for n in ("IN.txt", "admin1CodesASCII.txt", "admin2Codes.txt") if not (args.dir / n).exists()]
    if missing:
        print(f"missing in {args.dir}: {', '.join(missing)} (use --download)", file=sys.stderr)
        return 1

    only = {c.zfill(2) for c in args.admin1} if args.admin1 else None
    regions = _admin_regions(args.dir, only)
    places, aliases = _places(args.dir, only)
    print(f"{len(regions)} admin regions, {len(places)} places, {len(aliases)} aliases")
    if args.dry_run:
        return 0
    await _load(places, aliases, regions)
    print("gazetteer loaded")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
