// LOCAL DESIGN PREVIEW ONLY. Active only under `next dev` with
// NEXT_PUBLIC_UI_PREVIEW=1 (compiled out of production builds even if the
// variable were set). Lets the dashboard render on localhost, where Clerk
// cannot sign in, using the sample data below instead of the real API.
// Everything here is invented sample data for HushStays-style content.

import { ApiError } from "@/lib/api";

export const UI_PREVIEW = process.env.NODE_ENV !== "production" && process.env.NEXT_PUBLIC_UI_PREVIEW === "1";

const labels = { hot: "Hot", warm: "Warm", cold: "Cold", not_qualified: "Not qualified" };

const preferences = {
  transfer_number_mode: "single",
  call_summary_email: true,
  escalation_email: true,
  call_summary_subject: null,
  call_summary_body: null,
  stay_request_handling: "whatsapp",
  connect_request_handling: "live_transfer",
  busy_call_alert: true,
  guest_calling_alert: true,
  guest_reply_alert: true,
  lead_labels: labels,
};

const me = {
  id: "preview-user",
  email: "hello@hushstays.dev",
  name: "HushStays",
  phone: null,
  tier: "tier_1",
  status: "active",
  lead_exophone: "01141185313",
  twilio_lead_number: null,
  business_name: "HushStays",
  airbnb_host_status: null,
  property_count_estimate: 19,
  timezone: "Asia/Kolkata",
  terms_accepted_at: "2026-10-01T00:00:00Z",
  agent_first_message: null,
  agent_persona: null,
  agent_escalation_phrase: null,
  agent_handoff_phrase: null,
  agent_voice_gender: "female",
  notification_email: "ishank@hushstays.com",
  host_call_hours_enabled: false,
  host_call_hours_start: "09:00",
  host_call_hours_end: "21:00",
  host_call_hours_timezone: "Asia/Kolkata",
  discount_policy_text: null,
  negotiation_allowed: true,
  max_discount_percent_override: null,
  allow_pets: null,
  allow_early_checkin: null,
  photo_url: null,
  banner_url: null,
  notification_preferences: preferences,
  is_internal_org: true,
  interests: [],
};

type CapOverride = Partial<Record<string, unknown>>;
function cap(id: string, name: string, group: string, description: string, over: CapOverride = {}) {
  return {
    id, name, description, benefit: description, group,
    activation: "preference", enforcement: "advisory", selectable: true, available: true,
    enabled: true, state: "ready", setup_state: "complete", live_in_backend: true,
    requirements: [], missing_dependencies: [], integrations: [],
    can_enable: true, enable_blocked_reason: null, can_disable: true, disable_blocked_reason: null,
    effect: description, on_effect: "Mira uses this for your guests.", off_effect: "Mira skips this; your data is kept.",
    routes: [], onboarding_setup: null, config: {}, updated_at: null,
    ...over,
  };
}

const setupReq = (label: string, action: string, route: string) => [
  { id: label, label, hard: true, met: false, action_label: action, action_route: route },
];

const capabilities = {
  groups: [
    { id: "calls", name: "Guest calls & reception", description: "How Mira answers and routes guest calls." },
    { id: "bookings", name: "Bookings & pricing", description: "Availability, quotes and discounts." },
    { id: "ops", name: "Operations", description: "Guests, technicians and records." },
  ],
  capabilities: [
    cap("guest_support", "Guest Support voice agent", "calls", "Answers existing guests about their stay.", {
      enforcement: "always_on", selectable: false, state: "needs_setup", setup_state: "not_started",
      requirements: setupReq("A guest phone number assigned to a property", "Assign a number", "/dashboard/properties"),
    }),
    cap("lead_agent", "Portfolio Lead Agent", "calls", "Answers booking enquiries across your portfolio.", {
      can_disable: false, disable_blocked_reason: "Mira is still answering calls on your call intake number. Clear that number in Settings first -- turning this off here alone would not stop those calls.",
    }),
    cap("busy_recovery", "Busy-call recovery", "calls", "Follows up on guests who called while Mira was busy.", { enforcement: "always_on", selectable: false }),
    cap("host_handoff", "Host handoff & call transfer", "calls", "Hands live calls to you when a guest asks for you.", { enforcement: "always_on", selectable: false }),
    cap("quotes", "Price quotes", "bookings", "Gives guests a price for their dates.", { enforcement: "always_on", selectable: false }),
    cap("payments", "Online payments", "bookings", "Sends payment links and holds dates.", {
      state: "unavailable", requirements: setupReq("Your Razorpay account connected", "Connect Razorpay", "/dashboard/settings?tab=billing"),
    }),
    cap("negotiation", "Discount negotiation", "bookings", "Negotiates within the limits you set.", { state: "ready" }),
    cap("airbnb_pricing", "Live Airbnb pricing", "bookings", "Quotes today's Airbnb rate.", { enabled: false, state: "available", setup_state: "not_started" }),
    cap("calendar_sync", "Calendar sync", "ops", "Keeps availability in step with your iCal links.", {
      state: "needs_setup", setup_state: "not_started", requirements: setupReq("An iCal link on at least one property", "Add an iCal link", "/dashboard/properties"),
    }),
  ],
};

const billing = {
  encryption_ready: true,
  razorpay: { connected: false, key_id: null, mode: null, verified_at: null },
  templates: {
    payment_request: { label: "Payment request message", text: "Hi {guest_name}, pay here to confirm your stay: {link}", is_default: true, draft: null },
    payment_success: { label: "Payment received message", text: "Thank you {guest_name}, your stay is confirmed.", is_default: true, draft: null },
  },
  defaults: { payment_request: "Hi {guest_name}, pay here to confirm your stay: {link}", payment_success: "Thank you {guest_name}, your stay is confirmed." },
  placeholders: [{ name: "{guest_name}", help: "The guest's name" }, { name: "{link}", help: "The payment link" }],
  notify_host_on_payment: true,
  capability_enabled: true,
};

const properties = ["Hunzuru Wabi Sabi Villa near Manali", "The Slow-life Cottage & Bonnfire near Kasol", "Woodhouse Farm"].map((name, i) => ({
  id: `preview-prop-${i}`, user_id: "preview-user", name, display_name: name, city: ["Pangan", "Jari", "Nainital"][i],
  exophone: null, base_price: 30000, ical_url: null, usp: null, house_rules: null, neighborhood_info: null,
  faq: [], amenities: [], photos: [], max_guests: 10, host_transfer_phone: null,
}));

const technicians = [
  { id: "t1", property_id: "preview-prop-0", name: "Rakesh Thakur", specialty: "plumbing", phone: "+919800000001", rating: 4.5, created_at: "2026-10-02T00:00:00Z" },
  { id: "t2", property_id: "preview-prop-1", name: "Sunil Negi", specialty: "electrical", phone: "+919800000002", rating: 4.0, created_at: "2026-10-05T00:00:00Z" },
];

const overviewWidgets = {
  widgets: [
    { id: "snapshot", name: "Portfolio snapshot", description: "Calls, leads and bookings at a glance.", capabilities: [], sizes: ["full"], size: "full", hidden: false, hideable: true, default_visible: true, available: true, section: "summary", unavailable_reason: null },
    { id: "live", name: "Live requests", description: "Guests waiting on you right now.", capabilities: [], sizes: ["half", "full"], size: "half", hidden: false, hideable: true, default_visible: true, available: true, section: "live", unavailable_reason: null },
    { id: "calls", name: "Recent calls", description: "The latest calls Mira took.", capabilities: [], sizes: ["half", "full"], size: "half", hidden: false, hideable: true, default_visible: true, available: true, section: "details", unavailable_reason: null },
  ],
  revision: 1, is_default: true, updated_at: null,
};

const onboarding = {
  status: "completed", source: "legacy", current_step: "review", completed_steps: [],
  selected_capabilities: [], first_property: null, completed_at: "2026-10-01T00:00:00Z", selection_notes: [],
};

const notificationSettings = {
  preferences,
  placeholders: ["guest_name", "guest_phone", "property_name", "lead_label", "call_summary", "call_time", "call_duration", "dashboard_link"],
  default_subject: "[{lead_label}] Call summary: {property_name}",
  default_body: "Guest {guest_name} called about {property_name}.",
};

const nav = (id: string, label: string, placement = "movable") => ({
  id, label, href: id === "overview" ? "/dashboard" : `/dashboard/${id}`, placement, hideable: placement === "movable",
  available: true, hidden: false, capabilities: [], unavailable_reason: null,
});
const navigation = {
  items: [
    nav("overview", "Overview", "pinned_top"), nav("analytics", "Analytics"), nav("properties", "Properties"), nav("agent", "Agent"),
    nav("pricing", "Pricing"), nav("calendar", "Calendar"), nav("calls", "Calls"), nav("leads", "Leads"), nav("guests", "Guests"),
    nav("faq", "FAQ"), nav("settings", "Settings", "pinned_bottom"),
  ],
  revision: 1, is_default: true, updated_at: null,
};

const routes: Array<[RegExp, unknown]> = [
  [/^\/preferences\/navigation/, navigation],
  [/^\/auth\/me\/call-hours-status/, { enabled: false, current_owner: "MIRA", checked_at: "2026-11-09T10:00:00Z" }],
  [/^\/auth\/me/, me],
  [/^\/capabilities/, capabilities],
  [/^\/onboarding/, onboarding],
  [/^\/preferences\/overview-widgets/, overviewWidgets],
  [/^\/notification-settings/, notificationSettings],
  [/^\/properties/, properties],
  [/^\/technicians/, technicians],
  [/^\/billing/, billing],
  [/^\/payments/, []],
  [/^\/negotiation-rules/, []],
  [/^\/notifications/, []],
];

export async function previewResponse<T>(path: string, options: RequestInit): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const clean = path.split("?")[0];
  if (method !== "GET") return {} as T; // saves are accepted and ignored in preview
  const hit = routes.find(([re]) => re.test(clean));
  if (!hit) throw new ApiError(404, "Not available in design preview");
  return hit[1] as T;
}
