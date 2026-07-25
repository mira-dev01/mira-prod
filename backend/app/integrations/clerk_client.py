"""Clerk Backend API client -- used exactly once per Clerk user, the first
time their `clerk_user_id` is seen and no local User row is linked to it yet
(see app/auth/dependencies.py's get_current_user). Clerk's default session
JWT carries `sub` (Clerk user id) and, when an org is active, `org_id`, but
NOT the user's email -- fetching that requires this one Backend API call.
After the first successful resolution, `clerk_user_id` is stored on the
local User row and every later request resolves straight from the token via
a DB lookup, no Clerk API call involved.
"""

import httpx

from app.config import settings

_BASE_URL = "https://api.clerk.com/v1"


class ClerkError(Exception):
    """Raised for any non-2xx response or unexpected shape from Clerk."""


async def fetch_user_email(clerk_user_id: str, timeout: float = 10.0) -> str | None:
    if not settings.clerk_secret_key:
        raise ClerkError("CLERK_SECRET_KEY is not configured")

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            f"{_BASE_URL}/users/{clerk_user_id}",
            headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
        )
        if response.status_code >= 400:
            raise ClerkError(f"fetch user failed ({response.status_code}): {response.text}")
        data = response.json()

    primary_id = data.get("primary_email_address_id")
    for entry in data.get("email_addresses") or []:
        if entry.get("id") == primary_id:
            return entry.get("email_address")
    # Fall back to the first email on file if Clerk ever omits/mismatches
    # primary_email_address_id -- better than failing a real login over it.
    emails = data.get("email_addresses") or []
    return emails[0].get("email_address") if emails else None
