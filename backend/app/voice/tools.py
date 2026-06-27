"""Pipecat-facing wrappers around the 6 LLM tool functions.

Each wrapper is a "direct function" -- Pipecat extracts its name, description,
and parameter schema from the type hints and docstring below, so there's no
separate JSON tool-schema to keep in sync (see app/services/tool_handlers.py
for the actual business logic, which is unchanged from the Vapi-based setup
and reused as-is here).

`call_session_id` isn't something the LLM knows, so it's captured via the
`build_voice_tools` factory closure rather than being a tool parameter.
"""

import uuid
from typing import Literal

from pydantic import ValidationError

from pipecat.services.llm_service import FunctionCallParams

from app.database import AsyncSessionLocal
from app.schemas.tool import (
    CheckCalendarArgs,
    DispatchTechnicianArgs,
    EscalateToHostArgs,
    GetPricingArgs,
    NegotiateRateArgs,
    SendWhatsappArgs,
)
from app.services import tool_handlers

Urgency = Literal["low", "medium", "high", "emergency"]
IssueType = Literal["plumbing", "electrical", "ac", "wifi", "lock", "general"]
GuestLoyalty = Literal["new", "returning", "frequent"]

INVALID_ARGS_MESSAGE = "I'm missing some details to do that -- could you repeat the dates/details?"


def build_voice_tools(call_session_id: uuid.UUID | None, property_id: uuid.UUID | None) -> list:
    """Build the 6 tool functions for one call, bound to its call_session_id/property_id."""

    async def check_calendar(
        params: FunctionCallParams,
        property_id: str,
        check_in: str,
        check_out: str,
        num_guests: int | None = None,
    ):
        """Check if property is available for given dates.

        Args:
            property_id: The property's id, as given to you in your instructions.
            check_in: Check-in date, ISO format (YYYY-MM-DD).
            check_out: Check-out date, ISO format (YYYY-MM-DD).
            num_guests: Number of guests, if known.
        """
        async with AsyncSessionLocal() as db:
            try:
                args = CheckCalendarArgs(
                    property_id=property_id, check_in=check_in, check_out=check_out, num_guests=num_guests
                )
                result = await tool_handlers.handle_check_calendar(db, args)
            except ValidationError:
                result = INVALID_ARGS_MESSAGE
        await params.result_callback(result)

    async def get_pricing(
        params: FunctionCallParams,
        property_id: str,
        check_in: str,
        check_out: str,
        num_guests: int,
        apply_discounts: bool = True,
    ):
        """Get total price including base rate, cleaning fee, taxes.

        Args:
            property_id: The property's id, as given to you in your instructions.
            check_in: Check-in date, ISO format (YYYY-MM-DD).
            check_out: Check-out date, ISO format (YYYY-MM-DD).
            num_guests: Number of guests.
            apply_discounts: Whether to apply any matching pricing rules.
        """
        async with AsyncSessionLocal() as db:
            try:
                args = GetPricingArgs(
                    property_id=property_id,
                    check_in=check_in,
                    check_out=check_out,
                    num_guests=num_guests,
                    apply_discounts=apply_discounts,
                )
                result = await tool_handlers.handle_get_pricing(db, args)
            except ValidationError:
                result = INVALID_ARGS_MESSAGE
        await params.result_callback(result)

    async def dispatch_technician(
        params: FunctionCallParams,
        property_id: str,
        issue_type: IssueType,
        urgency: Urgency,
        guest_phone: str | None = None,
    ):
        """Call a local technician for physical issues.

        Args:
            property_id: The property's id, as given to you in your instructions.
            issue_type: One of plumbing, electrical, ac, wifi, lock, general.
            urgency: One of low, medium, high, emergency.
            guest_phone: The guest's phone number, if known.
        """
        async with AsyncSessionLocal() as db:
            try:
                args = DispatchTechnicianArgs(
                    property_id=property_id, issue_type=issue_type, urgency=urgency, guest_phone=guest_phone
                )
                result = await tool_handlers.handle_dispatch_technician(db, args, call_session_id)
            except ValidationError:
                result = INVALID_ARGS_MESSAGE
        await params.result_callback(result)

    async def send_whatsapp(
        params: FunctionCallParams,
        phone: str,
        message: str,
        template_name: str | None = None,
    ):
        """Send WhatsApp message to guest or host.

        Args:
            phone: Phone number to message.
            message: Message body.
            template_name: Optional WhatsApp template name.
        """
        async with AsyncSessionLocal() as db:
            try:
                args = SendWhatsappArgs(phone=phone, message=message, template_name=template_name)
                result = await tool_handlers.handle_send_whatsapp(db, args, property_id, call_session_id)
            except ValidationError:
                result = INVALID_ARGS_MESSAGE
        await params.result_callback(result)

    async def escalate_to_host(
        params: FunctionCallParams,
        property_id: str,
        reason: str,
        urgency: Urgency,
        guest_phone: str | None = None,
        call_summary: str | None = None,
    ):
        """Escalate urgent issue to property host.

        Args:
            property_id: The property's id, as given to you in your instructions.
            reason: Why this needs the host's attention.
            urgency: One of low, medium, high, emergency.
            guest_phone: The guest's phone number, if known.
            call_summary: A short summary of the call so far.
        """
        async with AsyncSessionLocal() as db:
            try:
                args = EscalateToHostArgs(
                    property_id=property_id,
                    reason=reason,
                    urgency=urgency,
                    guest_phone=guest_phone,
                    call_summary=call_summary,
                )
                result = await tool_handlers.handle_escalate_to_host(db, args, call_session_id)
            except ValidationError:
                result = INVALID_ARGS_MESSAGE
        await params.result_callback(result)

    async def negotiate_rate(
        params: FunctionCallParams,
        property_id: str,
        check_in: str,
        check_out: str,
        guest_offer: float,
        num_guests: int | None = None,
        guest_loyalty: GuestLoyalty = "new",
    ):
        """Calculate negotiated rate based on rules and occupancy.

        Args:
            property_id: The property's id, as given to you in your instructions.
            check_in: Check-in date, ISO format (YYYY-MM-DD).
            check_out: Check-out date, ISO format (YYYY-MM-DD).
            guest_offer: The rate the guest is offering, in INR.
            num_guests: Number of guests, if known.
            guest_loyalty: One of new, returning, frequent.
        """
        async with AsyncSessionLocal() as db:
            try:
                args = NegotiateRateArgs(
                    property_id=property_id,
                    check_in=check_in,
                    check_out=check_out,
                    guest_offer=guest_offer,
                    num_guests=num_guests,
                    guest_loyalty=guest_loyalty,
                )
                result = await tool_handlers.handle_negotiate_rate(db, args)
            except ValidationError:
                result = INVALID_ARGS_MESSAGE
        await params.result_callback(result)

    return [
        check_calendar,
        get_pricing,
        dispatch_technician,
        send_whatsapp,
        escalate_to_host,
        negotiate_rate,
    ]
