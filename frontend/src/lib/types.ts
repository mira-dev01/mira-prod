export type UserOut = {
  id: string;
  email: string;
  name: string | null;
  phone: string | null;
  tier: string;
  status: string;
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
  house_rules: string | null;
  faq: FAQItem[];
  amenities: string[];
  check_in_time: string;
  check_out_time: string;
  max_guests: number;
  vapi_assistant_id: string | null;
  vapi_phone_number_id: string | null;
  created_at: string;
};

export type PropertyCreate = {
  name: string;
  city?: string | null;
  exophone?: string | null;
  base_price: number;
  ical_url?: string | null;
  house_rules?: string | null;
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
  vapi_call_id: string | null;
  property_id: string | null;
  guest_profile_id: string | null;
  caller_number: string | null;
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
