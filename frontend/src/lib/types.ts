// Self-reported only -- MIRA has no Airbnb API access to verify this.
export type AirbnbHostStatus =
  | "new_host"
  | "individual_host"
  | "superhost"
  | "guest_favorite"
  | "professional_host"
  | "prefer_not_to_say";

// Maps to a specific Sarvam bulbul:v3 speaker name server-side
// (app/voice/pipeline.py's VOICE_BY_GENDER) -- not a Sarvam-native concept.
export type AgentVoiceGender = "female" | "male";

export type UserOut = {
  id: string;
  email: string;
  name: string | null;
  phone: string | null;
  tier: string;
  status: string;
  lead_exophone: string | null;
  business_name: string | null;
  airbnb_host_status: AirbnbHostStatus | null;
  property_count_estimate: number | null;
  timezone: string;
  terms_accepted_at: string | null;
  agent_first_message: string | null;
  agent_persona: string | null;
  agent_escalation_phrase: string | null;
  agent_voice_gender: AgentVoiceGender;
  notification_email: string | null;
  discount_policy_text: string | null;
  negotiation_allowed: boolean;
  max_discount_percent_override: number | null;
  allow_pets: boolean | null;
  allow_early_checkin: boolean | null;
  follow_up_channel_preference: string | null;
  photo_url: string | null;
  banner_url: string | null;
  whatsapp_assist_enabled: boolean;
  // Whether the request's active Clerk org matches the configured dev org --
  // gates dev-only features (currently just "Talk to Mira").
  is_internal_org: boolean;
};

export type UserUpdate = {
  name?: string | null;
  phone?: string | null;
  lead_exophone?: string | null;
  business_name?: string | null;
  airbnb_host_status?: AirbnbHostStatus | null;
  property_count_estimate?: number | null;
  timezone?: string | null;
  agent_first_message?: string | null;
  agent_persona?: string | null;
  agent_escalation_phrase?: string | null;
  agent_voice_gender?: AgentVoiceGender;
  notification_email?: string | null;
  discount_policy_text?: string | null;
  negotiation_allowed?: boolean | null;
  max_discount_percent_override?: number | null;
  allow_pets?: boolean | null;
  allow_early_checkin?: boolean | null;
  follow_up_channel_preference?: string | null;
  whatsapp_assist_enabled?: boolean | null;
};

// Unified negotiation/pricing training rule -- replaces what used to be two
// separate types (HostDiscountRule's host-wide discount triggers,
// PropertyPricingRule's stay-pricing rules). The three discount_* rule
// types are host-wide by definition (property_ids stays [] for them); the
// other five require an explicit, host-picked property_ids selection
// before they take effect. See backend NegotiationRule's docstring.
export type NegotiationRuleType =
  | "discount_no_ask"
  | "discount_guest_requests"
  | "discount_repeat_guest"
  | "length_of_stay"
  | "minimum_stay_nights"
  | "early_checkin_fee"
  | "late_checkout_fee"
  | "custom";
export type NegotiationRuleStatus = "pending_validation" | "approved" | "rejected";

// One entry in an OPTIONAL, host-defined negotiation ladder (backend
// NegotiationStage, app/schemas/negotiation_rule.py). order is the
// host-defined sequence position (0-based); value is that stage's
// authorized percent -- same unit as discount_percent, just one of an
// ordered list instead of a single scalar. No fixed count/value assumed
// anywhere this type is used.
export type NegotiationStage = {
  order: number;
  value: number;
};

export type NegotiationRuleOut = {
  id: string;
  host_id: string;
  rule_type: string;
  // unknown, not number -- a "custom" rule's condition is freeform (LLM
  // extraction is instructed to produce "whatever the host described, in
  // your own best structured guess"), so it can legitimately hold
  // non-numeric values. Matches PricingRuleOut.condition's own typing below.
  condition: Record<string, unknown>;
  discount_percent: number | null;
  // null for every rule authored before this field existed and for any
  // host who never describes a multi-step pushback progression -- see
  // NegotiationStage's own comment. When populated (length >= 2), this
  // takes precedence over discount_percent for the same rule (backend
  // ratified decision: staged supersedes flat).
  stages: NegotiationStage[] | null;
  label: string | null;
  property_ids: string[];
  source: string;
  status: string;
  raw_source_text: string | null;
  created_at: string;
};

export type NegotiationPolicyParseResponse = {
  rules: NegotiationRuleOut[];
};

export type NegotiationRuleUpdate = {
  rule_type?: NegotiationRuleType;
  condition?: Record<string, unknown>;
  discount_percent?: number | null;
  stages?: NegotiationStage[] | null;
  label?: string | null;
  property_ids?: string[];
  status?: NegotiationRuleStatus;
};

export type HostOnboarding = {
  name: string;
  phone?: string | null;
  business_name?: string | null;
  business_phone: string;
  airbnb_host_status?: AirbnbHostStatus | null;
  property_count_estimate?: number | null;
  airbnb_url: string;
  ical_url?: string | null;
  agent_first_message?: string | null;
};

export type HostOnboardingResponse = {
  snapshot_id: string | null;
  import_error: string | null;
};

export type FAQItem = { question: string; answer: string };

export type SeasonalNote = { note: string; start_month: number; end_month: number };

// Call Ownership Schedule (Phase 1-3) -- who owns an inbound guest call for
// this property. "MIRA"/"HOST" apply unconditionally; "SCHEDULED"
// alternates by time of day per call_handling_schedule_start/_end
// (property-local wall-clock, evaluated in `timezone`). Actual call
// routing is not implemented yet (Phase 4+) -- this is configuration only.
export type CallHandlingMode = "MIRA" | "HOST" | "SCHEDULED";

export type PropertyOut = {
  id: string;
  user_id: string;
  name: string;
  city: string | null;
  exophone: string | null;
  base_price: number;
  ical_url: string | null;
  usp: string | null;
  house_rules: string | null;
  neighborhood_info: string | null;
  faq: FAQItem[];
  amenities: string[];
  photos: string[];
  seasonal_notes: SeasonalNote[];
  check_in_time: string;
  check_out_time: string;
  max_guests: number;
  minimum_nights: number;
  saturday_minimum_stay_enabled: boolean;
  airbnb_listing_id: string | null;
  smart_price_estimate: number | null;
  smart_price_sample_size: number;
  smart_price_updated_at: string | null;
  exact_airbnb_pricing: boolean;
  is_premium: boolean;
  call_handling_mode: CallHandlingMode;
  call_handling_schedule_start: string | null;
  call_handling_schedule_end: string | null;
  timezone: string;
  created_at: string;
};

export type PropertyImportResult = {
  filename: string;
  status: "created" | "updated" | "error";
  property: PropertyOut | null;
  faq_entries_created: number;
  error: string | null;
};

export type AirbnbUrlImportStatus = {
  status: "running" | "ready" | "failed";
  results: PropertyImportResult[];
  error: string | null;
};

export type PropertyCreate = {
  name: string;
  city?: string | null;
  exophone?: string | null;
  base_price: number;
  ical_url?: string | null;
  usp?: string | null;
  house_rules?: string | null;
  neighborhood_info?: string | null;
  faq?: FAQItem[];
  amenities?: string[];
  photos?: string[];
  seasonal_notes?: SeasonalNote[];
  check_in_time?: string;
  check_out_time?: string;
  max_guests?: number;
  minimum_nights?: number;
  saturday_minimum_stay_enabled?: boolean;
  exact_airbnb_pricing?: boolean;
  is_premium?: boolean;
  // Same as exact_airbnb_pricing/is_premium above -- not on the backend's
  // PropertyCreate schema (Phase 1 decision: a schedule shouldn't be
  // required at property-creation time), only on PropertyUpdate. Included
  // here anyway since this type doubles as both panels' shared form shape;
  // extra keys sent on create are silently ignored by Pydantic (no
  // extra="forbid" on PropertyCreate), same as those two fields already
  // rely on.
  call_handling_mode?: CallHandlingMode;
  call_handling_schedule_start?: string | null;
  call_handling_schedule_end?: string | null;
  timezone?: string;
};

export type PropertyUpdate = Partial<PropertyCreate>;

// Set by call_classification_service (backend) via on_pipeline_finished once
// the call ends. "Qualified" is never itself a value here -- it's the
// grouping of BOOKING_LEAD/GUEST_SUPPORT/EXISTING_BOOKING/GENERAL_QUERY,
// computed wherever needed (see QUALIFIED_CALL_TYPES usage in calls page).
export type CallType =
  | "BOOKING_LEAD"
  | "GUEST_SUPPORT"
  | "EXISTING_BOOKING"
  | "GENERAL_QUERY"
  | "JUNK"
  | "INCOMPLETE"
  | "UNKNOWN";

export type CallSummary = {
  booking_snapshot: {
    guest_name: string;
    intent: string;
    check_in: string;
    check_out: string;
    nights: string;
    guests: string;
    property: string[];
    budget: string;
    occasion: string;
    language: string;
    room_number: string;
  };
  conversation_summary: string;
  outcome: {
    status: string;
    reason: string;
  };
  host_action: string[];
  key_details: string[];
  missing_information: string[];
};

export type CallSessionOut = {
  id: string;
  exotel_call_id: string | null;
  property_id: string | null;
  guest_profile_id: string | null;
  caller_number: string | null;
  guest_name: string | null;
  guest_phone: string | null;
  duration_minutes: number | null;
  recording_url: string | null;
  transcript: string | null;
  ai_summary: CallSummary | null;
  status: string;
  urgency: string | null;
  call_type: CallType;
  classification_confidence: number | null;
  classification_reason: string | null;
  revenue_attributed: number;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
};

export type CallSessionDetailOut = CallSessionOut & {
  escalations: NotificationOut[];
};

export type ConversationSummaryEntry = {
  call_session_id: string;
  property_id: string | null;
  property_name: string | null;
  date: string;
  summary: string;
  lead_temperature: string | null;
};

export type GuestProfileOut = {
  id: string;
  phone: string;
  name: string | null;
  total_stays: number;
  preferences: Record<string, unknown>;
  notes: string | null;
  last_property_id: string | null;
  preferred_language: string | null;
  last_outcome: string | null;
  last_follow_up: string | null;
  last_call_at: string | null;
  conversation_summaries: ConversationSummaryEntry[];
  created_at: string;
};

export type GuestRecentCall = {
  id: string;
  property_id: string | null;
  property_name: string | null;
  status: string;
  ai_summary: CallSummary | null;
  started_at: string | null;
};

export type GuestProfileDetailOut = GuestProfileOut & {
  lifetime_revenue: number;
  recent_calls: GuestRecentCall[];
};

export type GuestProfileUpdate = {
  name?: string | null;
  preferences?: Record<string, unknown> | null;
  notes?: string | null;
};

export type BookingOut = {
  id: string;
  property_id: string;
  guest_phone: string | null;
  guest_name: string | null;
  check_in: string;
  check_out: string;
  platform: string;
  status: string;
  created_at: string;
};

export type BookingCreate = {
  property_id: string;
  guest_phone?: string | null;
  guest_name?: string | null;
  check_in: string;
  check_out: string;
  platform?: string;
};

export type PricingRuleOut = {
  id: string;
  property_id: string;
  rule_type: string;
  condition: Record<string, unknown>;
  discount_percent: number;
  active: boolean;
  created_at: string;
};

export type PricingRuleCreate = {
  property_id: string;
  rule_type: string;
  condition?: Record<string, unknown>;
  discount_percent: number;
  active?: boolean;
};

export type TechnicianOut = {
  id: string;
  property_id: string;
  name: string;
  specialty: string;
  phone: string;
  rating: number;
  created_at: string;
};

export type TechnicianCreate = {
  property_id: string;
  name: string;
  specialty: string;
  phone: string;
  rating?: number;
};

export type NotificationOut = {
  id: string;
  property_id: string | null;
  property_name: string | null;
  call_session_id: string | null;
  channel: string;
  urgency: string;
  message: string;
  status: string;
  created_at: string;
};

// Mirrors app/services/pricing_engine.PriceBreakdown exactly -- no synthetic
// markup (weekend surge/cleaning fee/tax) exists on any property, on or off
// exact_airbnb_pricing; every property quotes its stored rate (or a live
// SearchApi fetch for exact_airbnb_pricing properties) as-is. Don't add
// fields here the backend dataclass doesn't actually have.
export type PriceBreakdown = {
  nights: number;
  base_total: number;
  discount_percent: number;
  discount_amount: number;
  total: number;
  per_night_avg: number;
};

export type AnalyticsSummary = {
  window_days: number;
  start_date: string | null;
  end_date: string | null;
  total_calls: number;
  completed_calls: number;
  escalated_calls: number;
  open_notifications: number;
  pipeline_value: number;
  open_leads: number;
  answer_rate: number | null;
};

export type AnalyticsTimeseriesMetric =
  | "total_calls"
  | "completed_calls"
  | "escalated_calls"
  | "pipeline_value"
  | "open_leads";

export type AnalyticsTimeseriesPoint = {
  date: string;
  value: number;
};

export type AnalyticsTimeseries = {
  metric: AnalyticsTimeseriesMetric;
  points: AnalyticsTimeseriesPoint[];
};

export type RecoveryFunnelStage = {
  stage: "busy_calls" | "recovered" | "converted";
  label: string;
  value: number;
};

export type RecoveryAnalytics = {
  window_days: number;
  start_date: string | null;
  end_date: string | null;
  busy_calls: number;
  recovered: number;
  converted: number;
  lost: number;
  avg_recovery_time_seconds: number | null;
  avg_host_response_seconds: number | null;
  recovery_rate: number | null;
  conversion_rate: number | null;
  funnel: RecoveryFunnelStage[];
};

export type LeadOut = {
  id: string;
  user_id: string;
  call_session_id: string | null;
  guest_name: string | null;
  phone: string | null;
  email: string | null;
  check_in: string | null;
  check_out: string | null;
  num_guests: number | null;
  purpose_of_stay: string | null;
  budget: number | null;
  preferred_location: string | null;
  properties_discussed: string[];
  questions_asked: string[];
  support_requests: string[];
  lead_temperature: string | null;
  lead_source: string;
  // How the guest reached Mira (phone_call today; a future WhatsApp-inbound
  // or web-widget lead would use a different value here).
  entry_channel: string;
  // Why this lead needed system-driven recovery instead of coming from a
  // normal completed conversation -- null for the common case. See
  // backend/app/models/lead.py's own comment for the full lead_source/
  // entry_channel/recovery_reason split (deliberately not a Lead.status
  // value). "BUSY_CALL" | "AFTER_HOURS" | "HOST_CALLBACK" | "GUEST_CALLBACK"
  // when set, kept as `string` here (not a union) to match how this file
  // already treats every other enum-like Lead field (status, lead_temperature).
  recovery_reason: string | null;
  conversation_summary: string | null;
  next_follow_up: string | null;
  escalated: boolean;
  transferred_to_host: boolean;
  status: string;
  occasion: string | null;
  created_at: string;
  updated_at: string;
  // Not a Lead column -- backfilled from the most recent linked Notification
  // (see backend/app/api/v1/leads.py::_with_urgency). null for a lead with
  // no escalation.
  urgency: string | null;
};

export type LeadStatus = "open" | "contacted" | "booked" | "closed";

// Backs the Service Requests tab (dashboard/leads/page.tsx, "Live
// Requests") -- one row per GUEST_SUPPORT-classified CallSession, not a
// stored table of its own. See backend/app/schemas/service_request.py.
export type ServiceRequestOut = {
  call_session_id: string;
  property_id: string | null;
  property_name: string | null;
  room_number: string | null;
  message: string;
  urgency: string;
  created_at: string;
  dismissed_at: string | null;
};

export type LeadUpdate = {
  guest_name?: string | null;
  lead_temperature?: "hot" | "warm" | "cold" | null;
  next_follow_up?: string | null;
  conversation_summary?: string | null;
  transferred_to_host?: boolean | null;
  status?: LeadStatus | null;
};

export type FaqEntryOut = {
  id: string;
  user_id: string;
  property_id: string | null;
  question: string;
  answer: string;
  category: string | null;
  status: "pending" | "verified";
  verified_by: string | null;
  created_at: string;
  updated_at: string;
};

export type FaqEntryCreate = {
  property_id?: string | null;
  question: string;
  answer: string;
  category?: string | null;
  status?: "pending" | "verified";
  verified_by?: string | null;
};

export type FaqEntryUpdate = Partial<FaqEntryCreate>;

export type FaqGapOut = {
  sample_id: string;
  question: string;
  count: number;
  property_id: string | null;
  last_asked_at: string;
  suggested_faq_entry_id: string | null;
  suggested_answer: string | null;
  match_score: number | null;
};

export type FaqGapAnswer = {
  answer: string;
  apply_to_property?: boolean;
};

export type FaqGapAnalytics = {
  most_frequent: { question: string; count: number }[];
  by_property: { property_id: string | null; count: number }[];
  over_time: { bucket: string; count: number }[];
};
