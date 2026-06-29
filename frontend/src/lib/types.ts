export type UserOut = {
  id: string;
  email: string;
  name: string | null;
  phone: string | null;
  tier: string;
  status: string;
  lead_exophone: string | null;
  agent_first_message: string | null;
  agent_persona: string | null;
  agent_escalation_phrase: string | null;
};

export type UserUpdate = {
  name?: string | null;
  phone?: string | null;
  lead_exophone?: string | null;
  agent_first_message?: string | null;
  agent_persona?: string | null;
  agent_escalation_phrase?: string | null;
};

export type FAQItem = { question: string; answer: string };

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
  check_in_time: string;
  check_out_time: string;
  max_guests: number;
  airbnb_listing_id: string | null;
  created_at: string;
};

export type PropertyImportResult = {
  filename: string;
  status: "created" | "updated" | "error";
  property: PropertyOut | null;
  faq_entries_created: number;
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
  check_in_time?: string;
  check_out_time?: string;
  max_guests?: number;
};

export type PropertyUpdate = Partial<PropertyCreate>;

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
  ai_summary: string | null;
  status: string;
  urgency: string | null;
  revenue_attributed: number;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
};

export type GuestProfileOut = {
  id: string;
  phone: string;
  name: string | null;
  total_stays: number;
  preferences: Record<string, unknown>;
  notes: string | null;
  created_at: string;
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
  call_session_id: string | null;
  channel: string;
  urgency: string;
  message: string;
  status: string;
  created_at: string;
};

export type PriceBreakdown = {
  nights: number;
  base_total: number;
  weekend_nights: number;
  cleaning_fee: number;
  tax_amount: number;
  discount_percent: number;
  discount_amount: number;
  total: number;
  per_night_avg: number;
};

export type AnalyticsSummary = {
  window_days: number;
  total_calls: number;
  completed_calls: number;
  escalated_calls: number;
  open_notifications: number;
  revenue_attributed: number;
  answer_rate: number | null;
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
  conversation_summary: string | null;
  next_follow_up: string | null;
  escalated: boolean;
  transferred_to_host: boolean;
  created_at: string;
  updated_at: string;
};

export type LeadUpdate = {
  guest_name?: string | null;
  lead_temperature?: "hot" | "warm" | "cold" | null;
  next_follow_up?: string | null;
  conversation_summary?: string | null;
  transferred_to_host?: boolean | null;
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
