"""One-shot provisioning script for the Exotel <-> Vapi SIP trunk integration.

Run this once real Vapi/Exotel/Groq/Deepgram/ElevenLabs keys are in `.env`:

    python -m scripts.setup_vapi_assistant --exophone +9180XXXXXXXX

What it does on the Vapi side (verified against current Vapi API docs):
  1. Registers BYO provider credentials for Groq/Deepgram/ElevenLabs, so
     usage bills to the host's own accounts instead of Vapi's marked-up keys.
  2. Creates a `byo-sip-trunk` credential pointing at Exotel's SIP gateway.
  3. Creates a `byo-phone-number` resource for the given ExoPhone DID, bound
     to that credential, with NO static assistantId -- this makes Vapi send
     an `assistant-request` webhook on every inbound call, which is how we
     do per-call dynamic context (property/guest lookup, see
     app/services/call_service.py).
  4. Stores the returned phone-number id on the matching `properties` row
     (matched by exophone) so assistant-request lookups are exact.

What you must still do by hand (no safe public API to automate -- see
README "Exotel <-> Vapi SIP trunk setup"):
  - Create the Exotel-side SIP trunk + DID mapping (dashboard or Exotel
    support ticket) using the values this script prints.
  - In the Vapi dashboard -> Org Settings, set "Server URL" to
    {BACKEND_BASE_URL}/api/v1/webhooks/vapi and "Server Secret" to
    VAPI_WEBHOOK_SECRET, so assistant-request has an account-level
    destination for numbers with no bound assistant.
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.integrations.vapi_client import VapiClient
from app.models.property import Property


async def ensure_provider_credentials(client: VapiClient) -> None:
    providers = [
        ("groq", settings.groq_api_key),
        ("deepgram", settings.deepgram_api_key),
        ("11labs", settings.elevenlabs_api_key),
    ]
    for provider, api_key in providers:
        if not api_key:
            print(f"  [skip] {provider}: no API key in .env")
            continue
        result = await client.create_credential({"provider": provider, "apiKey": api_key})
        print(f"  [ok]   {provider} credential created: {result.get('id')}")


async def ensure_sip_trunk_credential(client: VapiClient) -> str:
    if not (settings.exotel_gateway_ip and settings.exotel_gateway_port):
        sys.exit("EXOTEL_GATEWAY_IP and EXOTEL_GATEWAY_PORT must be set in .env before running this script.")

    payload = {
        "provider": "byo-sip-trunk",
        "name": "exotel-trunk",
        "gateways": [
            {
                "ip": settings.exotel_gateway_ip,
                "port": settings.exotel_gateway_port,
                "inboundEnabled": True,
                "outboundEnabled": True,
            }
        ],
        "outboundLeadingPlusEnabled": True,
    }
    result = await client.create_credential(payload)
    credential_id = result["id"]
    print(f"  [ok]   byo-sip-trunk credential created: {credential_id}")
    return credential_id


async def ensure_phone_number(client: VapiClient, credential_id: str, exophone: str) -> str:
    payload = {
        "provider": "byo-phone-number",
        "name": f"mira-{exophone}",
        "number": exophone,
        "numberE164CheckEnabled": False,
        "credentialId": credential_id,
    }
    result = await client.create_phone_number(payload)
    phone_number_id = result["id"]
    print(f"  [ok]   byo-phone-number created: {phone_number_id} ({exophone})")
    return phone_number_id


async def store_phone_number_id(exophone: str, phone_number_id: str) -> None:
    async with AsyncSessionLocal() as db:
        property_ = await db.scalar(select(Property).where(Property.exophone == exophone))
        if property_ is None:
            print(f"  [warn] no properties row has exophone={exophone} -- create the property first, "
                  f"then re-run with --link-only {phone_number_id}")
            return
        property_.vapi_phone_number_id = phone_number_id
        await db.commit()
        print(f"  [ok]   linked properties.vapi_phone_number_id for '{property_.name}'")


def print_exotel_checklist(sip_subdomain: str) -> None:
    print("\n--- Apply on the Exotel side (dashboard / support ticket) ---")
    print(f"  Trunk domain_name : {settings.exotel_sid}.pstn.exotel.com")
    print(f"  Destination URI   : {sip_subdomain}.sip.vapi.ai:5060;transport=tcp")
    print("  Whitelist Vapi IPs: 44.229.228.186/32, 44.238.177.138/32")
    print("  Map your ExoPhone DID to this trunk.")
    print("\n--- Apply once in the Vapi dashboard ---")
    print(f"  Org Settings -> Server URL: {settings.backend_base_url}/api/v1/webhooks/vapi")
    print(f"  Org Settings -> Server Secret: {settings.vapi_webhook_secret}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exophone", required=True, help="E.164 ExoPhone DID, e.g. +9180XXXXXXXX")
    args = parser.parse_args()

    if not settings.vapi_api_key:
        sys.exit("VAPI_API_KEY must be set in .env")
    if not settings.vapi_sip_subdomain:
        sys.exit("VAPI_SIP_SUBDOMAIN must be set in .env (your Vapi SIP subdomain, e.g. 'mira-prod')")

    client = VapiClient()

    print("1. Registering bring-your-own provider credentials...")
    await ensure_provider_credentials(client)

    print("2. Creating BYO SIP trunk credential for Exotel...")
    credential_id = await ensure_sip_trunk_credential(client)

    print("3. Creating phone number resource...")
    phone_number_id = await ensure_phone_number(client, credential_id, args.exophone)

    print("4. Linking phone number id to the matching property row...")
    await store_phone_number_id(args.exophone, phone_number_id)

    print_exotel_checklist(settings.vapi_sip_subdomain)


if __name__ == "__main__":
    asyncio.run(main())
