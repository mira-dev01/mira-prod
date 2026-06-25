"""Single endpoint for all Vapi server messages: assistant-request (per-call
dynamic assistant config), tool-calls (the 6 tool functions), end-of-call-report
(transcript/summary persistence), and status-update (lifecycle logging).

Configure this same URL as both the phone number's `assistant-request`
destination and the assistant's `server.url` -- see scripts/setup_vapi_assistant.py.
"""

import logging

from fastapi import APIRouter, Depends, Header, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.integrations.vapi_client import verify_webhook_secret
from app.schemas.tool import TOOL_ARG_MODELS
from app.services import call_service, tool_handlers

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/vapi", tags=["webhooks"])

TOOL_HANDLERS = {
    "check_calendar": lambda db, args, ctx: tool_handlers.handle_check_calendar(db, args),
    "get_pricing": lambda db, args, ctx: tool_handlers.handle_get_pricing(db, args),
    "dispatch_technician": lambda db, args, ctx: tool_handlers.handle_dispatch_technician(
        db, args, ctx["call_session_id"]
    ),
    "send_whatsapp": lambda db, args, ctx: tool_handlers.handle_send_whatsapp(
        db, args, ctx["property_id"], ctx["call_session_id"]
    ),
    "escalate_to_host": lambda db, args, ctx: tool_handlers.handle_escalate_to_host(
        db, args, ctx["call_session_id"]
    ),
    "negotiate_rate": lambda db, args, ctx: tool_handlers.handle_negotiate_rate(db, args),
}


def _extract_tool_calls(message: dict) -> list[dict]:
    """Vapi has shipped a couple of shapes for this over time. Support both:
    the native `toolCallList` (id/name/arguments flat) and the OpenAI-style
    `toolCalls` (id/function.name/function.arguments, arguments as JSON string)."""
    calls = []

    for call in message.get("toolCallList") or []:
        calls.append({"id": call.get("id"), "name": call.get("name"), "arguments": call.get("arguments") or {}})

    if not calls:
        import json

        for call in message.get("toolCalls") or []:
            fn = call.get("function") or {}
            arguments = fn.get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {}
            calls.append({"id": call.get("id"), "name": fn.get("name"), "arguments": arguments})

    return calls


async def _handle_assistant_request(db: AsyncSession, call: dict) -> dict:
    property_ = await call_service.resolve_property_for_call(db, call)
    if property_ is None:
        logger.warning("assistant-request: no property matched for call %s", call.get("id"))
        return {
            "error": "No property is configured for this phone number yet. Please contact the host directly."
        }

    caller_number = call_service.extract_caller_number(call)
    guest = await call_service.get_or_create_guest_profile(db, caller_number)

    await call_service.get_or_create_call_session(
        db,
        vapi_call_id=call.get("id"),
        property_id=property_.id,
        guest_profile_id=guest.id if guest else None,
        caller_number=caller_number,
    )

    return {"assistant": call_service.build_transient_assistant_config(property_, guest)}


async def _handle_tool_calls(db: AsyncSession, message: dict) -> dict:
    call = message.get("call") or {}
    vapi_call_id = call.get("id")

    session = None
    if vapi_call_id:
        session = await call_service.get_or_create_call_session(
            db,
            vapi_call_id=vapi_call_id,
            property_id=None,
            guest_profile_id=None,
            caller_number=call_service.extract_caller_number(call),
        )
    ctx = {
        "call_session_id": session.id if session else None,
        "property_id": session.property_id if session else None,
    }

    results = []
    for tool_call in _extract_tool_calls(message):
        tool_call_id, name, raw_args = tool_call["id"], tool_call["name"], tool_call["arguments"]
        arg_model = TOOL_ARG_MODELS.get(name)
        handler = TOOL_HANDLERS.get(name)

        if arg_model is None or handler is None:
            result = f"Unknown tool '{name}'."
        else:
            try:
                args = arg_model(**raw_args)
                result = await handler(db, args, ctx)
            except ValidationError as exc:
                logger.info("Tool args validation failed for %s: %s", name, exc)
                result = "I'm missing some details to do that -- could you repeat the dates/details?"
            except Exception:  # noqa: BLE001 - never let one bad tool call kill the call
                logger.exception("Tool handler '%s' failed", name)
                result = "Something went wrong on my end handling that -- could you try again?"

        results.append({"toolCallId": tool_call_id, "result": result})

    return {"results": results}


async def _handle_end_of_call_report(db: AsyncSession, message: dict) -> dict:
    call = message.get("call") or {}
    vapi_call_id = call.get("id")
    if not vapi_call_id:
        return {"result": "ok"}

    transcript = message.get("transcript") or (message.get("artifact") or {}).get("transcript")
    summary = message.get("summary") or (message.get("analysis") or {}).get("summary")

    await call_service.finalize_call_session(db, vapi_call_id, transcript, summary, status="completed")
    return {"result": "ok"}


@router.post("")
async def vapi_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_vapi_secret: str | None = Header(default=None, alias="x-vapi-secret"),
) -> dict:
    if not verify_webhook_secret(x_vapi_secret):
        logger.warning("Rejected Vapi webhook with invalid/missing secret")
        return {"error": "unauthorized"}

    try:
        body = await request.json()
    except ValueError:
        return {"result": "ok"}

    message = body.get("message") or {}
    msg_type = message.get("type", "")

    if msg_type == "assistant-request":
        return await _handle_assistant_request(db, message.get("call") or {})

    if msg_type == "tool-calls":
        return await _handle_tool_calls(db, message)

    if msg_type == "end-of-call-report":
        return await _handle_end_of_call_report(db, message)

    # status-update and any other event types: acknowledge, nothing to do yet.
    return {"result": "ok"}
