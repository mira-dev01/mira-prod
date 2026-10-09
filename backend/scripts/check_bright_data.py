"""Checks whether a Bright Data API key can import Airbnb listings.

Usage (from backend/):
    python scripts/check_bright_data.py                      # free key check
    python scripts/check_bright_data.py --url <airbnb url>   # real scrape (costs a few cents)
    python scripts/check_bright_data.py --url <url> --wait   # ...and poll until the data is ready

The key comes from --key, else $BRIGHT_DATA_API_KEY, else backend/.env.
To test dev's key, copy it from Railway and pass it with --key.
A browser UI over the same checks: scripts/bright_data_checker_app.py.

The free check (diagnose()) runs several read-only calls and reports each:
  - key auth: a trigger with a deliberately invalid URL -- 400
    validation_error means the key authenticated, 401 means it didn't.
    Bright Data validates input BEFORE checking the account, so this alone
    does NOT prove the account can scrape (it passed while the account was
    suspended -- that's what the next check is for).
  - account status: GET /status -- "active" vs "suspended", plus the
    customer id the key belongs to (compare dev's key vs local's this way).
  - billing: GET /customer/balance (403 just means the key lacks billing
    permission, not that billing is broken).
  - dataset access: is the Airbnb dataset in GET /datasets/list.
None of these start a job or bill anything.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

BASE_URL = "https://api.brightdata.com/datasets/v3"
DATASET_ID = "gd_ld7ll037kqy322v05"  # same as app/integrations/bright_data_client.py
# Mirrors normalize_listing_url in app/integrations/bright_data_client.py --
# kept standalone so this script runs without the backend's settings/.env.
_ROOM_ID_RE = re.compile(r"/rooms/(?:plus/)?(\d+)")


def load_key(cli_key: str | None) -> tuple[str, str]:
    if cli_key:
        return cli_key.strip(), "--key"
    if os.environ.get("BRIGHT_DATA_API_KEY"):
        return os.environ["BRIGHT_DATA_API_KEY"].strip(), "$BRIGHT_DATA_API_KEY"
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("BRIGHT_DATA_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'"), str(env_file)
    sys.exit("No key found: pass --key, set BRIGHT_DATA_API_KEY, or add it to backend/.env")


def mask(key: str) -> str:
    return f"{key[:4]}...{key[-4:]} (length {len(key)})"


def normalize_url(raw: str) -> str | None:
    """https://www.airbnb.com/rooms/<id>, or None if it isn't a listing link."""
    match = _ROOM_ID_RE.search(raw.strip())
    if not match or "airbnb." not in raw.lower():
        return None
    return f"https://www.airbnb.com/rooms/{match.group(1)}"


def make_client(key: str) -> httpx.Client:
    return httpx.Client(timeout=30, headers={"Authorization": f"Bearer {key}"})


def trigger(client: httpx.Client, urls: list[str]) -> httpx.Response:
    return client.post(
        f"{BASE_URL}/trigger",
        params={"dataset_id": DATASET_ID, "format": "json"},
        json=[{"url": url} for url in urls],
    )


# Bright Data error text -> what it actually means / what to do. Matched as
# a case-insensitive substring of the response body.
KNOWN_ERRORS = {
    "customer is not active": (
        "The Bright Data ACCOUNT is suspended/inactive -- not a key problem. Every key on this "
        "account gets the same error. Usual causes: balance at $0 / free-trial credit used up or "
        "expired, no payment method on file, a failed charge, or the account held for "
        "verification/compliance review. Fix it in the Bright Data control panel "
        "(Billing, and any banner on the dashboard), or ask their support why it was suspended."
    ),
    "invalid credentials": "The key itself is wrong, deleted or revoked. Generate a new one under Account settings > API keys.",
    "lacks the required permissions": "The key works but its role doesn't allow this call (e.g. billing). Not a scraping problem by itself.",
    "snapshot does not exist": "The snapshot id is unknown -- wrong account's key, or the job was discarded (e.g. account suspended mid-job).",
    "validation_error": "Bright Data rejected an input URL's format.",
}


def explain(body: str) -> str | None:
    lowered = body.lower()
    for needle, meaning in KNOWN_ERRORS.items():
        if needle in lowered:
            return meaning
    return None


def _check(name: str, status: str, summary: str, resp: httpx.Response | None = None, hint: str | None = None) -> dict:
    """status: "ok" | "warn" | "fail"."""
    return {
        "check": name,
        "status": status,
        "summary": summary,
        "hint": hint or (explain(resp.text) if resp is not None and status != "ok" else None),
        "http": f"{resp.request.method} {resp.request.url} -> {resp.status_code}" if resp is not None else None,
        "body": resp.text[:2000] if resp is not None else None,
    }


def diagnose(client: httpx.Client) -> list[dict]:
    """Read-only checks, in the order that narrows the problem down fastest."""
    results = []

    resp = trigger(client, ["not-a-url"])
    if resp.status_code == 401:
        results.append(_check("Key authentication", "fail", "Key rejected (401)", resp))
        return results  # nothing else will work with a bad key
    if resp.status_code == 400 and "validation" in resp.text:
        results.append(_check("Key authentication", "ok", "Key authenticated (input validation reached)", resp))
    else:
        results.append(_check("Key authentication", "fail", f"Unexpected response ({resp.status_code})", resp))

    resp = client.get("https://api.brightdata.com/status")
    try:
        data = resp.json()
    except ValueError:
        data = {}
    account_status = data.get("status")
    customer = data.get("customer")
    if account_status == "active":
        results.append(_check("Account status", "ok", f"Account {customer} is active", resp))
    elif account_status:
        results.append(_check(
            "Account status", "fail",
            f"Account {customer} is '{account_status}' (can_make_requests={data.get('can_make_requests')})",
            resp, hint=KNOWN_ERRORS["customer is not active"],
        ))
    else:
        results.append(_check("Account status", "warn", f"Couldn't read account status ({resp.status_code})", resp))

    resp = client.get("https://api.brightdata.com/customer/balance")
    if resp.status_code == 200:
        results.append(_check("Billing balance", "ok", f"Balance: {resp.text[:200]}", resp))
    else:
        results.append(_check("Billing balance", "warn", f"Couldn't read balance ({resp.status_code})", resp))

    resp = client.get("https://api.brightdata.com/datasets/list")
    try:
        ids = {d.get("id") for d in resp.json()}
    except (ValueError, AttributeError):
        ids = set()
    if DATASET_ID in ids:
        results.append(_check("Airbnb dataset access", "ok", f"{DATASET_ID} is available to this key", resp))
    else:
        results.append(_check("Airbnb dataset access", "fail", f"{DATASET_ID} not in this key's dataset list ({resp.status_code})", resp))
    results[-1]["body"] = None  # the full catalogue is huge and not useful here

    return results


def check_key(client: httpx.Client) -> tuple[bool, str]:
    results = diagnose(client)
    failed = [r for r in results if r["status"] == "fail"]
    if failed:
        return False, "; ".join(f"{r['check']}: {r['summary']}" for r in failed)
    return True, "key authenticates and the account is active"


def start_scrape(client: httpx.Client, urls: list[str]) -> tuple[str | None, str]:
    resp = trigger(client, urls)
    if resp.status_code >= 400:
        meaning = explain(resp.text)
        return None, f"trigger rejected ({resp.status_code}): {resp.text}" + (f"\n  -> {meaning}" if meaning else "")
    snapshot_id = resp.json().get("snapshot_id")
    return snapshot_id, f"scrape started, snapshot_id={snapshot_id}"


def get_status(client: httpx.Client, snapshot_id: str) -> str:
    """"running" | "ready" | "failed" -- same mapping as the backend client."""
    status = (client.get(f"{BASE_URL}/progress/{snapshot_id}").json().get("status") or "").lower()
    if status in ("ready", "done", "completed"):
        return "ready"
    if status in ("failed", "error"):
        return "failed"
    return "running"


def get_records(client: httpx.Client, snapshot_id: str) -> list | dict:
    return client.get(f"{BASE_URL}/snapshot/{snapshot_id}", params={"format": "json"}).json()


def scrape(client: httpx.Client, url: str, wait: bool) -> bool:
    snapshot_id, message = start_scrape(client, [normalize_url(url) or url])
    print(f"{'OK  ' if snapshot_id else 'FAIL'}  {message}")
    if not snapshot_id or not wait:
        return bool(snapshot_id)

    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        status = get_status(client, snapshot_id)
        print(f"      status={status}")
        if status == "failed":
            print("FAIL  scrape failed")
            return False
        if status == "ready":
            break
        time.sleep(10)
    else:
        print("FAIL  still running after 5 minutes -- check again later with the snapshot_id")
        return False

    records = get_records(client, snapshot_id)
    if not isinstance(records, list) or not records:
        print(f"FAIL  no records returned: {json.dumps(records)[:500]}")
        return False
    first = records[0]
    print(f"OK    got {len(records)} record(s): name={first.get('name')!r} price={first.get('price')!r}")
    if first.get("error"):
        print(f"WARN  record carries an error: {first.get('error')}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key", help="Bright Data API key (defaults to env / backend/.env)")
    parser.add_argument("--url", help="Airbnb listing URL to actually scrape (billed)")
    parser.add_argument("--wait", action="store_true", help="with --url, poll until the scraped data is ready")
    args = parser.parse_args()

    key, source = load_key(args.key)
    print(f"Using key from {source}: {mask(key)}")

    with make_client(key) as client:
        results = diagnose(client)
        for r in results:
            print(f"{r['status'].upper():<5} {r['check']}: {r['summary']}")
            if r["http"]:
                print(f"      {r['http']}")
            if r["body"] and r["status"] != "ok":
                print(f"      body: {r['body'][:300]}")
            if r["hint"]:
                print(f"      -> {r['hint']}")
        ok = not any(r["status"] == "fail" for r in results)
        if ok and args.url:
            ok = scrape(client, args.url, args.wait)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
