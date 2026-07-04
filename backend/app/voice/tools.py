"""Pipecat-facing wrappers around the LLM tool functions.

Each wrapper is a "direct function" -- Pipecat extracts its name, description,
and parameter schema from the type hints and docstring below, so there's no
separate JSON tool-schema to keep in sync (see app/services/tool_handlers.py
for the actual business logic).

`call_session_id`/`property_id`/`host_user_id` aren't something the LLM
knows, so they're captured via the `build_voice_tools` factory closure rather
than being tool parameters.
"""

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager, nullcontext
from typing import AsyncIterator, Literal

from pydantic import ValidationError

from pipecat.frames.frames import MixerEnableFrame, TTSSpeakFrame
from pipecat.services.llm_service import FunctionCallParams

from app.database import AsyncSessionLocal
from app.schemas.tool import (
    CheckCalendarArgs,
    DispatchTechnicianArgs,
    EscalateToHostArgs,
    GetPricingArgs,
    LeadTemperature,
    NegotiateRateArgs,
    RecommendPropertiesArgs,
    SearchFaqArgs,
    SendWhatsappArgs,
    UpdateLeadArgs,
)
from app.services import tool_handlers
from app.voice.speaking_gate import BotSpeakingGate

logger = logging.getLogger(__name__)

Urgency = Literal["low", "medium", "high", "emergency"]
IssueType = Literal["plumbing", "electrical", "ac", "wifi", "lock", "general"]
GuestLoyalty = Literal["new", "returning", "frequent"]

INVALID_ARGS_MESSAGE = "I'm missing some details to do that -- could you repeat the dates/details?"


_FILLER_PLAYBACK_TIMEOUT = 5.0


@asynccontextmanager
async def _hold_music_span(params: FunctionCallParams, speaking_gate: BotSpeakingGate, phrase: str) -> AsyncIterator[None]:
    """Say a short filler phrase, wait for it to actually finish playing,
    play hold music for the duration of the `with` block, then stop the
    music before returning -- covers DB round-trip latency
    (check_calendar/get_pricing/update_lead) so the guest isn't sitting in
    silence while a tool runs.

    Ordering matters for the "no overlap" requirement: queue_frame() returns
    as soon as a frame is *queued*, not once its audio has played, so
    starting hold music right after queuing the TTSSpeakFrame would overlap
    it with the filler phrase itself. Waiting on speaking_gate first
    (BotSpeakingGate, in app/voice/speaking_gate.py) blocks until the
    phrase's audio has actually started and then finished.

    Holds speaking_gate.hold_music_lock for the whole span: pipecat runs
    same-turn function calls concurrently by default, and the system prompt
    tells the model to call update_lead silently on every new field, which
    can co-occur with check_calendar/get_pricing in the same turn. The lock
    serializes DB-touching tool calls' filler-phrase-through-music-off spans
    so two concurrent tools can never talk over each other's filler phrase
    or race on the shared mixer's on/off state.

    If TTS synthesis of the filler phrase fails/never starts (e.g. a Sarvam
    hiccup -- the same failure mode the greeting's own try/except in
    app/voice/pipeline.py already guards against), waiting on speaking_gate
    would otherwise hang forever and freeze the whole call. A bounded
    timeout means a filler-phrase failure just skips hold music for that one
    tool call instead of breaking the call.
    """
    worker = params.pipeline_worker
    async with speaking_gate.hold_music_lock:
        music_started = False
        try:
            await worker.queue_frame(TTSSpeakFrame(phrase))
            await asyncio.wait_for(speaking_gate.wait_for_utterance_to_finish(), timeout=_FILLER_PLAYBACK_TIMEOUT)
            await worker.queue_frame(MixerEnableFrame(True))
            music_started = True
        except Exception:
            logger.warning("Filler phrase / hold music setup failed; tool call continues without it.")
        try:
            yield
        finally:
            if music_started:
                await worker.queue_frame(MixerEnableFrame(False))


def build_voice_tools(
    call_session_id: uuid.UUID | None,
    property_id: uuid.UUID | None,
    host_user_id: uuid.UUID,
    speaking_gate: BotSpeakingGate,
) -> list:
    """Build the tool functions for one call, bound to its call_session_id/property_id/host_user_id.

    speaking_gate: shared with app/voice/pipeline.py's Pipeline for this same
    call -- lets a DB-touching tool below know when its own filler phrase has
    actually finished playing before it starts hold music (see
    _hold_music_span).
    """

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
        async with _hold_music_span(params, speaking_gate, "Sure, I'll quickly check that for you."):
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
        async with _hold_music_span(params, speaking_gate, "Sure, I'll quickly check that for you."):
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
                result = await tool_handlers.handle_escalate_to_host(db, args, call_session_id, host_user_id)
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

    async def recommend_properties(
        params: FunctionCallParams,
        budget: float | None = None,
        num_guests: int | None = None,
        preferred_location: str | None = None,
        purpose_of_stay: str | None = None,
    ):
        """Recommend properties from the portfolio matching the guest's needs.

        Args:
            budget: The guest's nightly budget in INR, if known.
            num_guests: Number of guests, if known.
            preferred_location: Preferred city/area, if known.
            purpose_of_stay: e.g. family trip, couples getaway, workcation.
        """
        async with AsyncSessionLocal() as db:
            args = RecommendPropertiesArgs(
                budget=budget,
                num_guests=num_guests,
                preferred_location=preferred_location,
                purpose_of_stay=purpose_of_stay,
            )
            result = await tool_handlers.handle_recommend_properties(db, args, host_user_id)
        await params.result_callback(result)

    async def update_lead(
        params: FunctionCallParams,
        guest_name: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        check_in: str | None = None,
        check_out: str | None = None,
        num_guests: int | None = None,
        purpose_of_stay: str | None = None,
        budget: float | None = None,
        preferred_location: str | None = None,
        lead_temperature: LeadTemperature | None = None,
        properties_discussed: list[str] | None = None,
        questions_asked: list[str] | None = None,
        support_requests: list[str] | None = None,
        conversation_summary: str | None = None,
        next_follow_up: str | None = None,
        escalated: bool | None = None,
        transferred_to_host: bool | None = None,
    ):
        """Save or update this guest's CRM lead record. Call this silently
        (don't narrate it to the guest) whenever you learn something new about
        them during the conversation.

        Args:
            guest_name: The guest's name, if known.
            phone: The guest's phone number, if known.
            email: The guest's email, if known.
            check_in: Desired check-in date, ISO format (YYYY-MM-DD), if known.
            check_out: Desired check-out date, ISO format (YYYY-MM-DD), if known.
            num_guests: Number of guests, if known.
            purpose_of_stay: e.g. family trip, couples getaway, workcation.
            budget: The guest's nightly budget in INR, if known.
            preferred_location: Preferred city/area, if known.
            lead_temperature: One of hot, warm, cold.
            properties_discussed: Property names or ids discussed so far.
            questions_asked: Questions the guest asked.
            support_requests: Any support requests raised.
            conversation_summary: A short summary of the conversation so far.
            next_follow_up: What the host should follow up on, if anything.
            escalated: Whether this call was escalated to the host.
            transferred_to_host: Whether the call was transferred to the host.
        """
        # update_lead fires silently on every field learned during the call
        # (per this tool's own instructions above) -- a filler phrase on
        # every single call would talk over the guest constantly. Only speak
        # up for the two fields the guest actually asked to have recorded
        # under them: name and phone number ("update our portal" reads as
        # "save my booking details", not "note my budget").
        announce_update = guest_name is not None or phone is not None
        hold_music = (
            _hold_music_span(params, speaking_gate, "One second, I will quickly update our portal for the same.")
            if announce_update
            else nullcontext()
        )
        async with hold_music:
            async with AsyncSessionLocal() as db:
                try:
                    args = UpdateLeadArgs(
                        guest_name=guest_name,
                        phone=phone,
                        email=email,
                        check_in=check_in,
                        check_out=check_out,
                        num_guests=num_guests,
                        purpose_of_stay=purpose_of_stay,
                        budget=budget,
                        preferred_location=preferred_location,
                        lead_temperature=lead_temperature,
                        properties_discussed=properties_discussed,
                        questions_asked=questions_asked,
                        support_requests=support_requests,
                        conversation_summary=conversation_summary,
                        next_follow_up=next_follow_up,
                        escalated=escalated,
                        transferred_to_host=transferred_to_host,
                    )
                    result = await tool_handlers.handle_update_lead(db, args, host_user_id, call_session_id)
                except ValidationError:
                    result = INVALID_ARGS_MESSAGE
        await params.result_callback(result)

    async def search_faq(
        params: FunctionCallParams,
        query: str,
        faq_property_id: str | None = None,
    ):
        """Search the verified FAQ knowledge base for an answer. Only answer
        property/support questions using this tool's results -- never guess.

        Args:
            query: The guest's question, in their own words.
            faq_property_id: The property's id, if the question is about a specific property.
        """
        async with AsyncSessionLocal() as db:
            args = SearchFaqArgs(query=query, property_id=faq_property_id)
            result = await tool_handlers.handle_search_faq(db, args, host_user_id, property_id)
        await params.result_callback(result)

    return [
        check_calendar,
        get_pricing,
        dispatch_technician,
        send_whatsapp,
        escalate_to_host,
        negotiate_rate,
        recommend_properties,
        update_lead,
        search_faq,
    ]
