"""Bright Data Web Scraper API client -- Airbnb dataset (gd_ld7ll037kqy322v05).

Async, 3-step flow (Bright Data's Datasets v3 API, confirmed via their docs
and sample dataset -- github.com/luminati-io/Airbnb-dataset-samples):
  1. trigger_scrape(urls) -> snapshot_id
  2. get_snapshot_status(snapshot_id) -> "running" | "ready" | "failed" (poll)
  3. get_snapshot_data(snapshot_id) -> list of raw listing records once ready

Takes individual Airbnb listing URLs, NOT a host-profile URL -- Bright Data
has no "discover every listing for this host" mode for Airbnb (unlike some
of their other scrapers, e.g. Amazon/Instagram). The onboarding flow built
on top of this (app/api/v1/properties.py) asks the host to paste each
listing URL rather than one profile link.
"""

import httpx

from app.config import settings

_BASE_URL = "https://api.brightdata.com/datasets/v3"
_DATASET_ID = "gd_ld7ll037kqy322v05"


class BrightDataError(Exception):
    """Raised for any non-2xx response or unexpected shape from Bright Data."""


def _headers() -> dict[str, str]:
    if not settings.bright_data_api_key:
        raise BrightDataError("BRIGHT_DATA_API_KEY is not configured")
    return {
        "Authorization": f"Bearer {settings.bright_data_api_key}",
        "Content-Type": "application/json",
    }


async def trigger_scrape(urls: list[str], timeout: float = 15.0) -> str:
    """Starts an async scrape job for the given Airbnb listing URLs.
    Returns a snapshot_id used to poll status and fetch results."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{_BASE_URL}/trigger",
            params={"dataset_id": _DATASET_ID, "format": "json"},
            headers=_headers(),
            json=[{"url": url} for url in urls],
        )
        if response.status_code >= 400:
            raise BrightDataError(f"trigger failed ({response.status_code}): {response.text}")
        data = response.json()
        snapshot_id = data.get("snapshot_id")
        if not snapshot_id:
            raise BrightDataError(f"trigger response missing snapshot_id: {data}")
        return snapshot_id


async def get_snapshot_status(snapshot_id: str, timeout: float = 15.0) -> str:
    """Returns "running", "ready", or "failed". Bright Data's progress
    endpoint's exact status vocabulary isn't independently confirmed against
    a live account in this codebase yet -- if a host's real key surfaces a
    different value, normalize it here rather than in the caller."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(f"{_BASE_URL}/progress/{snapshot_id}", headers=_headers())
        if response.status_code >= 400:
            raise BrightDataError(f"progress check failed ({response.status_code}): {response.text}")
        data = response.json()
        status = (data.get("status") or "").lower()
        if status in ("ready", "done", "completed"):
            return "ready"
        if status in ("failed", "error"):
            return "failed"
        return "running"


async def get_snapshot_data(snapshot_id: str, timeout: float = 30.0) -> list[dict]:
    """Fetches the final scraped records once get_snapshot_status() reports "ready"."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            f"{_BASE_URL}/snapshot/{snapshot_id}", params={"format": "json"}, headers=_headers()
        )
        if response.status_code >= 400:
            raise BrightDataError(f"snapshot fetch failed ({response.status_code}): {response.text}")
        data = response.json()
        if not isinstance(data, list):
            raise BrightDataError(f"expected a list of records, got: {type(data)}")
        return data
