"""Single source of truth for what HostWithMira can do, expressed as
capabilities -- not sidebar tabs.

The dashboard's tabs are views over shared backend services (one voice
pipeline, one tool set for every call, one lead store), so a capability here
describes a *functionality* and records honestly what turning it on or off
actually changes. Three activation kinds:

- "core": always on. Either the voice agent can't work without it, or it is
  a safety net for the "a genuine guest opportunity must not silently
  disappear" invariant (busy-call recovery, escalation, lead capture). Hosts
  can't disable these -- hiding a page in Phase 2 must never reach them.
- "bound": the enabled state IS an existing per-host column (e.g.
  negotiation <-> User.negotiation_allowed, already read by
  pricing_engine.negotiate_rate). Toggling writes that column; there is no
  second copy of the state.
- "preference": the host's stored choice (HostCapability row). Each one
  says in `enforcement` whether the backend actually honors the choice
  ("enforced") or whether the choice only drives setup/onboarding while the
  service keeps following its own configuration ("advisory"). Advisory
  capabilities that are still live in the backend (`live_requirement`)
  can't be marked disabled -- the UI would otherwise claim something is off
  that is still answering guests.

Readiness is never stored: requirement ids below are evaluated against real
data by app/services/capability_service.py on every read.

Phase 2 (navigation/widget customization) should read `routes` from here
rather than inventing a parallel tab -> feature mapping.
"""

from dataclasses import dataclass, field
from typing import Literal

ActivationKind = Literal["core", "bound", "preference"]
Enforcement = Literal["always_on", "enforced", "advisory"]


@dataclass(frozen=True)
class CapabilityGroup:
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class Requirement:
    """A data precondition, evaluated by capability_service._REQUIREMENT_CHECKS.
    hard=True: unmet means the capability is "needs_setup". hard=False: a
    recommendation -- shown as a setup action, never blocks "ready"."""

    id: str
    label: str
    hard: bool
    action_label: str
    action_route: str


@dataclass(frozen=True)
class IntegrationRef:
    """A platform-level integration (env-configured, not per host).
    required=True: without it the capability can't function at all
    ("unavailable"). required=False: the capability degrades gracefully
    (e.g. WhatsApp falls back to in-app notifications)."""

    id: str
    required: bool


@dataclass(frozen=True)
class Capability:
    id: str
    name: str
    description: str
    benefit: str
    group: str
    activation: ActivationKind
    enforcement: Enforcement
    # Pre-capability behavior for a host with no stored choice. Chosen so an
    # existing host's effective state is exactly what they had before.
    default_enabled: bool
    # What being on/off really does -- shown verbatim in Settings, so it
    # must never overstate (see module docstring).
    on_effect: str
    off_effect: str
    hard_dependencies: tuple[str, ...] = ()
    soft_dependencies: tuple[str, ...] = ()
    requirements: tuple[Requirement, ...] = ()
    integrations: tuple[IntegrationRef, ...] = ()
    # Dashboard routes that surface this capability. Several capabilities
    # share routes (Overview, Properties, AI Training, Settings) -- that's
    # expected and is exactly why routes aren't the unit of activation.
    routes: tuple[str, ...] = ()
    # Voice tools (app/voice/tools.py) that exercise this capability.
    # Informational: tool registration is NOT per-host conditional.
    voice_tools: tuple[str, ...] = ()
    # "bound" only: the User column holding the enabled state.
    bound_user_field: str | None = None
    # "preference"+"advisory" only: a requirement id whose truth means the
    # backend is already doing this for the host regardless of preference.
    live_requirement: str | None = None
    live_block_reason: str | None = None
    # Onboarding "Set up" section this capability contributes, if any.
    onboarding_setup: str | None = None
    # Allowlist for HostCapability.config. Empty = no capability-specific
    # config yet (settings with an existing home stay there).
    config_keys: dict[str, type] = field(default_factory=dict)

    @property
    def selectable(self) -> bool:
        return self.activation != "core"


GROUPS: tuple[CapabilityGroup, ...] = (
    CapabilityGroup("guest_calls", "Guest Calls & Reception", "How Mira answers and routes guest calls."),
    CapabilityGroup("bookings_pricing", "Bookings & Pricing", "Availability, quotes and discounts."),
    CapabilityGroup("guest_support", "Guest Support", "Helping guests during their stay."),
    CapabilityGroup("leads_crm", "Leads & Guest CRM", "Capturing enquiries and remembering guests."),
    CapabilityGroup("properties_knowledge", "Properties & Knowledge", "Your listings and what Mira knows about them."),
    CapabilityGroup("analytics", "Analytics & Insights", "Reporting on calls, leads and bookings."),
)

# ── Shared requirements ────────────────────────────────────────────────
_HAS_PROPERTY = Requirement("has_property", "At least one property added", True, "Add a property", "/dashboard/properties")
_PROPERTY_PHONE = Requirement(
    "property_phone", "A guest phone number assigned to a property", True, "Assign a number", "/dashboard/properties"
)
_HOST_PHONE_SOFT = Requirement(
    "host_phone", "A transfer number for alerts and live transfers", False, "Set it up", "/dashboard/settings"
)

CAPABILITIES: tuple[Capability, ...] = (
    # ── Guest Calls & Reception ────────────────────────────────────────
    Capability(
        id="guest_support_agent",
        name="Guest Support voice agent",
        description="Answers calls to each property's number: availability, prices, rules and FAQs.",
        benefit="Every guest call to a listing gets answered, day or night.",
        group="guest_calls",
        activation="core",
        enforcement="always_on",
        default_enabled=True,
        on_effect="Mira answers calls on every property number you've assigned.",
        off_effect="",
        hard_dependencies=("property_management",),
        requirements=(_HAS_PROPERTY, _PROPERTY_PHONE),
        routes=("/dashboard/calls", "/dashboard/properties", "/dashboard/properties/ai-training"),
        voice_tools=("check_calendar", "get_pricing", "search_faq", "lookup_booking", "end_call"),
        onboarding_setup="voice_intro",
    ),
    Capability(
        id="lead_agent",
        name="Portfolio Lead Agent",
        description="One number for your whole portfolio. Mira finds the right property for each caller.",
        benefit="Turn general booking enquiries into leads for whichever property fits.",
        group="guest_calls",
        activation="preference",
        enforcement="advisory",
        default_enabled=True,
        on_effect="Calls to your call intake number run the Lead Agent across all your properties.",
        off_effect="Removed from your setup checklist. The Lead Agent only answers while a call intake number is saved.",
        hard_dependencies=("property_management",),
        requirements=(
            Requirement("lead_number", "Call intake number saved", True, "Add the number", "/dashboard/settings"),
            _HAS_PROPERTY,
        ),
        routes=("/dashboard/settings", "/dashboard/leads"),
        voice_tools=("recommend_properties", "update_lead"),
        live_requirement="lead_number",
        live_block_reason=(
            "Mira is still answering calls on your call intake number. Clear that number in Settings first -- "
            "turning this off here alone would not stop those calls."
        ),
        onboarding_setup="lead_number",
    ),
    Capability(
        id="busy_call_recovery",
        name="Busy-call recovery",
        description="If Mira is already on a call, new callers get a WhatsApp follow-up and become a lead.",
        benefit="No guest is lost to a busy line.",
        group="guest_calls",
        activation="core",
        enforcement="always_on",
        default_enabled=True,
        on_effect="Busy callers get a WhatsApp menu and appear in Live Requests.",
        off_effect="",
        soft_dependencies=("lead_capture",),
        requirements=(_HOST_PHONE_SOFT,),
        integrations=(IntegrationRef("twilio_whatsapp", required=False), IntegrationRef("redis", required=False)),
        routes=("/dashboard/leads", "/dashboard/analytics"),
    ),
    Capability(
        id="host_handoff",
        name="Host handoff & call transfer",
        description="Mira escalates to you, or transfers the call, when a guest needs you.",
        benefit="Guests reach you when it matters, without you answering every call.",
        group="guest_calls",
        activation="core",
        enforcement="always_on",
        default_enabled=True,
        on_effect="Escalations reach you in-app, by email and WhatsApp; live transfers dial your transfer number.",
        off_effect="",
        requirements=(_HOST_PHONE_SOFT,),
        integrations=(IntegrationRef("twilio_whatsapp", required=False), IntegrationRef("email", required=False)),
        routes=("/dashboard/settings", "/dashboard/leads"),
        voice_tools=("escalate_to_host", "request_host_transfer"),
    ),
    # ── Bookings & Pricing ─────────────────────────────────────────────
    Capability(
        id="pricing_quotes",
        name="Price quotes",
        description="Quotes nightly rates, discounts and totals from your pricing.",
        benefit="Guests get an accurate quote on the call.",
        group="bookings_pricing",
        activation="core",
        enforcement="always_on",
        default_enabled=True,
        on_effect="Mira quotes from each property's base price, pricing rules or live Airbnb price.",
        off_effect="",
        hard_dependencies=("property_management",),
        requirements=(
            Requirement(
                "priceable_property",
                "A property with a base price or live Airbnb pricing",
                True,
                "Set a base price",
                "/dashboard/properties",
            ),
        ),
        routes=("/dashboard/pricing", "/dashboard/properties"),
        voice_tools=("get_pricing",),
    ),
    Capability(
        id="negotiation",
        name="Discount negotiation",
        description="Mira can offer discounts within the limits you set in AI Training.",
        benefit="Close price-sensitive guests without giving away more than you allow.",
        group="bookings_pricing",
        activation="bound",
        enforcement="enforced",
        default_enabled=True,
        on_effect="Mira may offer discounts within your limit and approved rules.",
        off_effect="Mira holds the quoted price and offers to connect the guest with you instead of discounting.",
        hard_dependencies=("pricing_quotes",),
        requirements=(
            Requirement(
                "negotiation_policy",
                "A discount limit or approved discount rules",
                False,
                "Set your discount policy",
                "/dashboard/properties/ai-training",
            ),
        ),
        routes=("/dashboard/properties/ai-training",),
        voice_tools=("negotiate_rate",),
        bound_user_field="negotiation_allowed",
        onboarding_setup="negotiation",
    ),
    Capability(
        id="live_airbnb_pricing",
        name="Live Airbnb pricing",
        description="Quotes a property's current Airbnb price instead of its base price.",
        benefit="Quotes always match what guests see on Airbnb.",
        group="bookings_pricing",
        activation="preference",
        enforcement="advisory",
        default_enabled=False,
        on_effect="Properties with live pricing switched on are quoted at their current Airbnb price.",
        off_effect="Removed from your setup checklist. Live pricing is controlled per property.",
        hard_dependencies=("pricing_quotes", "airbnb_import"),
        requirements=(
            Requirement(
                "live_pricing_property",
                "An imported Airbnb property with live pricing switched on",
                True,
                "Turn it on for a property",
                "/dashboard/properties",
            ),
        ),
        integrations=(IntegrationRef("searchapi", required=True),),
        routes=("/dashboard/properties", "/dashboard/pricing"),
        voice_tools=("get_pricing", "negotiate_rate"),
        live_requirement="live_pricing_property",
        live_block_reason=(
            "Some properties still quote live Airbnb prices. Switch live pricing off on those properties first."
        ),
        onboarding_setup="first_property",
    ),
    Capability(
        id="calendar_sync",
        name="Calendar sync",
        description="Syncs bookings from your iCal links so Mira never offers booked dates.",
        benefit="Mira never offers dates that are already booked elsewhere.",
        group="bookings_pricing",
        activation="preference",
        enforcement="advisory",
        default_enabled=True,
        on_effect="Every property with an iCal link is synced on a schedule.",
        off_effect=(
            "Removed from your setup checklist. Mira still checks availability from the bookings you record manually."
        ),
        hard_dependencies=("property_management",),
        requirements=(
            Requirement("ical_linked", "An iCal link on at least one property", True, "Add an iCal link", "/dashboard/properties"),
        ),
        routes=("/dashboard/calendar", "/dashboard/properties"),
        voice_tools=("check_calendar",),
        live_requirement="ical_linked",
        live_block_reason=(
            "Some properties still have iCal links, so their calendars keep syncing. Remove those links first -- "
            "otherwise Mira could offer dates that are already booked."
        ),
        onboarding_setup="first_property",
    ),
    # ── Guest Support ──────────────────────────────────────────────────
    Capability(
        id="technician_dispatch",
        name="Technician dispatch",
        description="Routes maintenance issues to the right technician and alerts you.",
        benefit="Maintenance issues get routed without you triaging every call.",
        group="guest_support",
        activation="preference",
        enforcement="enforced",
        default_enabled=True,
        on_effect="Mira names the best-matched technician to the guest and sends you a dispatch alert.",
        off_effect="Mira still logs the issue and flags it to you, but never names or suggests a technician.",
        hard_dependencies=("property_management",),
        requirements=(
            Requirement(
                "technician_on_file",
                "At least one technician added",
                True,
                "Add a technician",
                "/dashboard/settings?tab=technicians",
            ),
        ),
        routes=("/dashboard/settings?tab=technicians",),
        voice_tools=("dispatch_technician",),
        onboarding_setup="technicians",
    ),
    Capability(
        id="guest_messaging",
        name="Photos & WhatsApp follow-ups",
        description="Sends guests photos and details on WhatsApp during the call.",
        benefit="Guests see the place while they're still on the phone.",
        group="guest_support",
        activation="core",
        enforcement="always_on",
        default_enabled=True,
        on_effect="Mira can send photo galleries and follow-up messages on WhatsApp.",
        off_effect="",
        requirements=(
            Requirement("property_photos", "Photos on at least one property", False, "Add photos", "/dashboard/properties"),
        ),
        integrations=(IntegrationRef("twilio_whatsapp", required=False),),
        voice_tools=("send_whatsapp", "send_photos"),
    ),
    # ── Leads & Guest CRM ──────────────────────────────────────────────
    Capability(
        id="lead_capture",
        name="Live requests & lead capture",
        description="Turns every genuine enquiry into a lead you can follow up.",
        benefit="No booking enquiry silently disappears.",
        group="leads_crm",
        activation="core",
        enforcement="always_on",
        default_enabled=True,
        on_effect="Enquiries, escalations and busy-call recoveries appear in Live Requests and Opportunities.",
        off_effect="",
        routes=("/dashboard/leads", "/dashboard/opportunities"),
        voice_tools=("update_lead",),
    ),
    Capability(
        id="guest_memory",
        name="Guest memory",
        description="Recognises returning callers and remembers past stays.",
        benefit="Repeat guests get a personal welcome and repeat-guest pricing.",
        group="leads_crm",
        activation="core",
        enforcement="always_on",
        default_enabled=True,
        # The Guests page is only one view of this data -- repeat-guest
        # discounts in negotiation read it too, which is why it's core.
        on_effect="Returning callers are recognized; repeat-guest discount rules can apply.",
        off_effect="",
        routes=("/dashboard/guests",),
    ),
    # ── Properties & Knowledge ─────────────────────────────────────────
    Capability(
        id="property_management",
        name="Properties",
        description="Your listings: the details every other feature relies on.",
        benefit="One place to keep listing details accurate.",
        group="properties_knowledge",
        activation="core",
        enforcement="always_on",
        default_enabled=True,
        on_effect="Always available.",
        off_effect="",
        requirements=(_HAS_PROPERTY,),
        routes=("/dashboard/properties",),
        onboarding_setup="first_property",
    ),
    Capability(
        id="airbnb_import",
        name="Airbnb import",
        description="Creates properties from Airbnb links, with photos and FAQs.",
        benefit="Set up a property in a minute instead of typing it in.",
        group="properties_knowledge",
        activation="core",
        enforcement="always_on",
        default_enabled=True,
        on_effect="Import listings from the Properties page or during onboarding.",
        off_effect="",
        integrations=(IntegrationRef("bright_data", required=True),),
        routes=("/dashboard/properties",),
        onboarding_setup="first_property",
    ),
    Capability(
        id="knowledge_faq",
        name="Knowledge & FAQ answers",
        description="Answers from your verified FAQs and logs what Mira couldn't answer.",
        benefit="Fewer repeat questions reach you; gaps are easy to fill.",
        group="properties_knowledge",
        activation="core",
        enforcement="always_on",
        default_enabled=True,
        # The FAQ page is where hosts curate this -- hiding that page in
        # Phase 2 must not stop Mira answering from the knowledge base.
        on_effect="Mira searches your verified FAQs and listing details on every question.",
        off_effect="",
        requirements=(
            Requirement("faq_knowledge", "Verified FAQs or listing FAQs", False, "Review FAQs", "/dashboard/faq"),
        ),
        routes=("/dashboard/faq", "/dashboard/properties/ai-training"),
        voice_tools=("search_faq",),
    ),
    # ── Analytics & Insights ───────────────────────────────────────────
    Capability(
        id="analytics_reporting",
        name="Analytics & call history",
        description="Call outcomes, conversions and revenue, with transcripts.",
        benefit="See what Mira is doing for your business.",
        group="analytics",
        activation="core",
        enforcement="always_on",
        default_enabled=True,
        on_effect="Always available.",
        off_effect="",
        routes=("/dashboard", "/dashboard/analytics", "/dashboard/calls"),
    ),
    Capability(
        id="booking_reconciliation",
        name="Booking attribution",
        description="Links bookings to the calls that won them and confirms final prices.",
        benefit="Know which bookings Mira actually won you.",
        group="analytics",
        activation="core",
        enforcement="always_on",
        default_enabled=True,
        on_effect="New bookings from calendar sync or manual entry are queued for attribution review.",
        off_effect="",
        soft_dependencies=("calendar_sync",),
        routes=("/dashboard/calendar", "/dashboard/analytics"),
    ),
)

CAPABILITIES_BY_ID: dict[str, Capability] = {c.id: c for c in CAPABILITIES}
GROUPS_BY_ID: dict[str, CapabilityGroup] = {g.id: g for g in GROUPS}
SELECTABLE_CAPABILITY_IDS: tuple[str, ...] = tuple(c.id for c in CAPABILITIES if c.selectable)


def get_capability(capability_id: str) -> Capability | None:
    return CAPABILITIES_BY_ID.get(capability_id)


def dependents_of(capability_id: str) -> tuple[Capability, ...]:
    """Capabilities that hard-depend on capability_id."""
    return tuple(c for c in CAPABILITIES if capability_id in c.hard_dependencies)
