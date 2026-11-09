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

const photo = (c1: string, c2: string) =>
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="420"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${c1}"/><stop offset="1" stop-color="${c2}"/></linearGradient></defs><rect width="640" height="420" fill="url(#g)"/></svg>`
  );
const propertyPhotos = [
  [photo("#8a9a6b", "#d8cfae"), photo("#b8896a", "#e9dcc3"), photo("#6f8091", "#cbd5d9")],
  [photo("#a07f62", "#e6d7bd")],
  [],
];
const properties = ["Hunzuru Wabi Sabi Villa near Manali", "The Slow-life Cottage & Bonnfire near Kasol", "Woodhouse Farm"].map((name, i) => ({
  id: `preview-prop-${i}`, user_id: "preview-user", name, display_name: name, city: ["Pangan", "Jari", "Nainital"][i],
  exophone: i === 0 ? "+918041234567" : null, base_price: [30000, 14500, 22000][i], ical_url: i === 0 ? "https://www.airbnb.com/calendar/ical/123.ics" : null,
  usp: i === 0 ? "Glass-walled villa above the Beas with a private deck and fire pit" : null,
  house_rules: i === 0 ? "No loud music after 10 pm. Pets on request. Government ID at check-in." : null,
  neighborhood_info: i === 0 ? "10 min walk to the river. Cafes in Old Manali (15 min). Cab to the airport about 2 hours." : null,
  faq: [], amenities: i === 0 ? ["WiFi", "Fire pit", "Kitchen", "Parking"] : [], photos: propertyPhotos[i],
  seasonal_notes: i === 0 ? [{ note: "Extra heater provided.", start_month: 11, end_month: 2 }] : [],
  check_in_time: "14:00", check_out_time: "11:00", max_guests: [10, 6, 12][i], minimum_nights: i === 0 ? 2 : 1,
  saturday_minimum_stay_enabled: i === 0, room_rental_enabled: i === 1, room_base_price: i === 1 ? 4500 : null,
  rentable_room_count: i === 1 ? 3 : null, room_max_guests: i === 1 ? 2 : null, room_details: null, room_ical_url: null,
  airbnb_listing_id: null, smart_price_estimate: null, smart_price_sample_size: 0, smart_price_updated_at: null,
  exact_airbnb_pricing: i === 0, is_premium: i === 0, host_transfer_phone: null,
  call_handling_mode: "MIRA", call_handling_schedule_start: null, call_handling_schedule_end: null,
  timezone: "Asia/Kolkata", created_at: "2026-10-01T00:00:00Z",
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

const now = Date.now();
const ago = (h: number) => new Date(now - h * 3600_000).toISOString();

const overview = {
  start_date: "2026-10-10", end_date: "2026-11-09", has_properties: true,
  portfolio: {
    occupancy: 0.42, revenue: 684000, adr: 14200, revpar: 5964, upcoming_revenue: 392000, booked_nights: 241, available_nights: 574,
    completeness: { bookings_total: 14, bookings_priced: 11, is_complete: false },
    upcoming_completeness: { bookings_total: 9, bookings_priced: 9, is_complete: true },
    currency: "INR",
  },
  opportunities: { booking_opportunities: 23, high_intent: 6, booking_intent: 4 },
  mira_attributed_bookings: 3,
  activity: { total_calls: 87, completed_calls: 74, qualified_calls: 31, escalated_calls: 5, answer_rate: 0.93 },
  attention: { price_confirmations: 3, attribution_confirmations: 1, high_intent_unconverted: { count: 4, potential_value: 186000, valued_count: 3 } },
};

function lead(id: string, over: Record<string, unknown>) {
  return {
    id, user_id: "preview-user", call_session_id: `call-${id}`, guest_name: null, phone: null, email: null, check_in: null, check_out: null,
    num_guests: null, purpose_of_stay: null, budget: null, preferred_location: null, properties_discussed: [], questions_asked: [],
    support_requests: [], lead_temperature: "warm", lead_source: "phone", entry_channel: "phone_call", recovery_reason: null,
    conversation_summary: null, next_follow_up: null, escalated: false, transferred_to_host: false, status: "open", occasion: null,
    created_at: ago(30), updated_at: ago(6), urgency: null, ...over,
  };
}
const leads = [
  lead("l1", { guest_name: "Aarav Mehta", phone: "+919811100001", check_in: "2026-11-15", check_out: "2026-11-20", num_guests: 5, properties_discussed: ["Hunzuru Wabi Sabi near Manali"], lead_temperature: "hot", escalated: true, urgency: "high", conversation_summary: "Group of five wants Manali 15–20 Nov and asked for a call back about the villa rate.", updated_at: ago(1) }),
  lead("l2", { guest_name: "Priya Nair", phone: "+919811100002", check_in: "2026-12-24", check_out: "2026-12-28", num_guests: 8, properties_discussed: ["Woodhouse Farm"], lead_temperature: "hot", escalated: true, urgency: "medium", conversation_summary: "Family trip for Christmas, wants to confirm the extra-bed charge.", updated_at: ago(3) }),
  lead("l3", { guest_name: "Rohan Gupta", phone: "+919811100003", properties_discussed: ["The Slow-life Cottage & Bonnfire near Kasol"], lead_temperature: "warm", status: "contacted", updated_at: ago(9) }),
  lead("l4", { guest_name: "Sana Khan", phone: "+919811100004", check_in: "2026-11-30", check_out: "2026-12-02", num_guests: 4, properties_discussed: ["Jungle House Jaipur"], lead_temperature: "hot", status: "booked", updated_at: ago(20) }),
  lead("l5", { guest_name: "Vikram Rao", phone: "+919811100005", lead_temperature: "cold", status: "closed", updated_at: ago(70) }),
  lead("l6", { guest_name: "Meera Iyer", phone: "+919811100006", recovery_reason: "BUSY_CALL", lead_temperature: "warm", properties_discussed: ["Vayu: A Tranquil Himalayan Home"], conversation_summary: "Called while the line was busy; WhatsApp follow-up sent, asked about Mukteshwar in January.", updated_at: ago(2) }),
];
const calls = [
  { id: "c1", exotel_call_id: "x1", property_id: null, guest_profile_id: null, caller_number: "+919811100001", guest_name: "Aarav Mehta", guest_phone: "+919811100001", duration_minutes: 6.4, recording_url: null, transcript: null, ai_summary: null, status: "completed", urgency: "high", call_type: "BOOKING_LEAD", classification_confidence: 0.9, classification_reason: null, revenue_attributed: 0, started_at: ago(1), ended_at: ago(1), created_at: ago(1) },
  { id: "c2", exotel_call_id: "x2", property_id: null, guest_profile_id: null, caller_number: "+919811100002", guest_name: "Priya Nair", guest_phone: "+919811100002", duration_minutes: 4.1, recording_url: null, transcript: null, ai_summary: null, status: "completed", urgency: null, call_type: "BOOKING_LEAD", classification_confidence: 0.9, classification_reason: null, revenue_attributed: 0, started_at: ago(3), ended_at: ago(3), created_at: ago(3) },
  { id: "c3", exotel_call_id: "x3", property_id: null, guest_profile_id: null, caller_number: "+919811100009", guest_name: null, guest_phone: "+919811100009", duration_minutes: 0.4, recording_url: null, transcript: null, ai_summary: null, status: "completed", urgency: null, call_type: "JUNK", classification_confidence: 0.9, classification_reason: null, revenue_attributed: 0, started_at: ago(5), ended_at: ago(5), created_at: ago(5) },
];
const faqGaps = [
  { sample_id: "g1", question: "Is there a bonfire we can book for a group?", count: 3, property_id: "preview-prop-1", last_asked_at: ago(8), suggested_faq_entry_id: null, suggested_answer: null, match_score: null, source: "search_miss" },
  { sample_id: "g2", question: "Do you allow pets at Woodhouse Farm?", count: 2, property_id: "preview-prop-2", last_asked_at: ago(30), suggested_faq_entry_id: null, suggested_answer: null, match_score: null, source: "guard_blocked" },
];
const serviceRequests = [
  { call_session_id: "c9", property_id: "preview-prop-0", property_name: "Hunzuru Wabi Sabi near Manali", room_number: "2", message: "Geyser not working in room 2.", urgency: "high", created_at: ago(4), dismissed_at: null },
];

const faqEntries = [
  { id: "f1", user_id: "preview-user", property_id: "preview-prop-0", group_id: null, question: "Is breakfast included?", answer: "Breakfast is not included; the caretaker can cook on request for ₹400 a head.", category: null, status: "verified", verified_by: "host", created_at: ago(500), updated_at: ago(500) },
  { id: "f2", user_id: "preview-user", property_id: "preview-prop-0", group_id: null, question: "How far is the nearest ATM?", answer: "About 4 km, in Old Manali.", category: null, status: "verified", verified_by: "host", created_at: ago(400), updated_at: ago(400) },
];

const policyItems = [
  { id: "po1", topic: "pets", title: "", text: "Small pets are welcome with prior notice; a ₹1,000 cleaning fee applies.", status: "active", property_id: null, property_name: null, source_document_id: null, source_filename: null, source_excerpt: null, review_notes: [], created_at: ago(900), updated_at: ago(900) },
  { id: "po2", topic: "parties", title: "", text: "Parties and loud music are not allowed after 10 pm.", status: "active", property_id: "preview-prop-0", property_name: "Hunzuru Wabi Sabi Villa near Manali", source_document_id: null, source_filename: null, source_excerpt: null, review_notes: [], created_at: ago(800), updated_at: ago(800) },
];
const policies = {
  rows: [
    { topic: "pets", label: "Pets", hint: "Whether pets are allowed and any fee", state: "everyone", override_count: 0, items: [policyItems[0]] },
    { topic: "parties", label: "Parties & events", hint: "Groups, music and quiet hours", state: "property_only", override_count: 1, items: [policyItems[1]] },
    { topic: "cancellation", label: "Cancellation", hint: "Refunds and changes", state: "unset", override_count: 0, items: [] },
  ],
  drafts: [],
  documents: [],
  properties: properties.map((pr, i) => ({ id: pr.id, name: pr.name, city: pr.city, labels: i === 0 ? [{ key: "no_parties", label: "No parties" }, { key: "pets_ok", label: "Pets welcome" }] : [{ key: "pets_ok", label: "Pets welcome" }], policy_ids: i === 0 ? ["po1", "po2"] : ["po1"] })),
  topics: [{ key: "pets", label: "Pets", hint: "" }, { key: "parties", label: "Parties & events", hint: "" }, { key: "cancellation", label: "Cancellation", hint: "" }],
};

const callDetail = {
  ...calls[0],
  transcript: "assistant: Namaste, this is Mira from HushStays. How can I help?\nuser: Hi, we are five people and want a villa near Manali from 15 to 20 November.\nassistant: Hunzuru Wabi Sabi sleeps ten and is free those dates. It is ₹30,000 a night.\nuser: Can someone call me back about the rate?",
  ai_summary: {
    booking_snapshot: { guest_name: "Aarav Mehta", intent: "Book a villa", check_in: "15 Nov", check_out: "20 Nov", nights: "5", guests: "5", property: ["Hunzuru Wabi Sabi near Manali"], budget: "Not stated", occasion: "Friends trip", language: "English", room_number: "—" },
    conversation_summary: "Group of five wants Manali 15 to 20 November and asked for a call back about the villa rate.",
    outcome: { status: "Follow-up Required", reason: "Guest asked the host to call back about the rate." },
    host_action: ["Call back about the nightly rate"], key_details: ["Villa is free on those dates"], missing_information: ["Budget"], objection_tags: ["price"],
  },
  escalations: [
    { id: "n1", channel: "escalation", message: "Guest wants a call back about the villa rate for 15 to 20 Nov.", urgency: "high", status: "new", created_at: ago(1) },
  ],
};

const routes: Array<[RegExp, unknown]> = [
  [/^\/analytics\/overview/, overview],
  [/^\/leads\/service-requests/, serviceRequests],
  [/^\/leads/, leads],
  [/^\/calls\/[^/]+/, callDetail],
  [/^\/calls/, calls],
  [/^\/faq\/gaps|^\/faq-gaps|^\/unanswered/, faqGaps],
  [/^\/tickets/, []],
  [/^\/bookings\/reconciliation/, { items: [], total: 0 }],
  [/^\/preferences\/navigation/, navigation],
  [/^\/auth\/me\/call-hours-status/, { enabled: false, current_owner: "MIRA", checked_at: "2026-11-09T10:00:00Z" }],
  [/^\/auth\/me/, me],
  [/^\/capabilities/, capabilities],
  [/^\/onboarding/, onboarding],
  [/^\/preferences\/overview-widgets/, overviewWidgets],
  [/^\/notification-settings/, notificationSettings],
  [/^\/policies/, policies],
  [/^\/faq$/, faqEntries],
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
