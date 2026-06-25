"""Wrapper around Vapi.ai's Management API.

Used by scripts/setup_vapi_assistant.py to provision the BYO SIP trunk
credential + phone number + assistant that bridges Exotel calls straight
into Vapi (see README for the full Exotel<->Vapi SIP trunk setup). Inbound
call handling itself happens via webhooks at
app/api/v1/webhooks/vapi.py, not through this client.
"""

import hmac
from typing import Any

import httpx

from app.config import settings

VAPI_BASE_URL = "https://api.vapi.ai"


def verify_webhook_secret(secret_header: str | None) -> bool:
    if not secret_header:
        return False
    return hmac.compare_digest(secret_header, settings.vapi_webhook_secret)


class VapiClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.vapi_api_key
        if not self.api_key:
            raise RuntimeError("VAPI_API_KEY is not configured")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method, f"{VAPI_BASE_URL}{path}", headers=self._headers(), **kwargs
            )
            response.raise_for_status()
            return response.json() if response.content else {}

    async def create_credential(self, payload: dict) -> dict:
        return await self._request("POST", "/credential", json=payload)

    async def create_phone_number(self, payload: dict) -> dict:
        return await self._request("POST", "/phone-number", json=payload)

    async def create_assistant(self, payload: dict) -> dict:
        return await self._request("POST", "/assistant", json=payload)

    async def update_assistant(self, assistant_id: str, payload: dict) -> dict:
        return await self._request("PATCH", f"/assistant/{assistant_id}", json=payload)

    async def create_call(self, payload: dict) -> dict:
        return await self._request("POST", "/call", json=payload)

    async def get_call(self, call_id: str) -> dict:
        return await self._request("GET", f"/call/{call_id}")
