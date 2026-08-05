import type {
  AirbnbUrlImportStatus,
  AnalyticsSummary,
  AnalyticsTimeseries,
  AnalyticsTimeseriesMetric,
  BookingCreate,
  BookingOut,
  CallSessionDetailOut,
  CallSessionOut,
  FaqEntryCreate,
  FaqEntryOut,
  FaqEntryUpdate,
  FaqGapAnalytics,
  FaqGapAnswer,
  FaqGapOut,
  DiscountPolicyParseResponse,
  GuestProfileDetailOut,
  GuestProfileOut,
  GuestProfileUpdate,
  HostDiscountRuleOut,
  HostDiscountRuleUpdate,
  HostOnboarding,
  HostOnboardingResponse,
  LeadOut,
  LeadUpdate,
  NotificationOut,
  ServiceRequestOut,
  PriceBreakdown,
  PricingPolicyParseResponse,
  PricingRuleCreate,
  PricingRuleOut,
  PropertyCreate,
  PropertyImportResult,
  PropertyOut,
  PropertyPricingRuleOut,
  PropertyPricingRuleUpdate,
  PropertyUpdate,
  TechnicianCreate,
  TechnicianOut,
  UserOut,
  UserUpdate,
} from "@/lib/types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

// Clerk's session token is only obtainable from its React hooks
// (useAuth().getToken()), not from a plain module-level function -- this is
// how AuthProvider (lib/auth-context.tsx) hands that async getter down to
// every request/uploadFiles/uploadAudio call below without threading it
// through every single api.* call site.
let tokenGetter: (() => Promise<string | null>) | null = null;

export function setTokenGetter(fn: (() => Promise<string | null>) | null): void {
  tokenGetter = fn;
}

export async function getToken(): Promise<string | null> {
  if (!tokenGetter) return null;
  return tokenGetter();
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = await getToken();
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // ignore non-JSON error bodies
    }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function buildQuery(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

async function uploadFiles<T>(path: string, files: File[], fieldName: string = "files"): Promise<T> {
  const token = await getToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const formData = new FormData();
  files.forEach((file) => formData.append(fieldName, file));

  // No Content-Type set here -- the browser fills in multipart/form-data
  // with the correct boundary itself, which it can only do if we don't
  // set the header manually.
  const res = await fetch(`${API_BASE_URL}${path}`, { method: "POST", headers, body: formData });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // ignore non-JSON error bodies
    }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }

  return (await res.json()) as T;
}

async function uploadAudio<T>(path: string, audio: Blob, filename: string): Promise<T> {
  const token = await getToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const formData = new FormData();
  formData.append("audio", audio, filename);

  const res = await fetch(`${API_BASE_URL}${path}`, { method: "POST", headers, body: formData });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // ignore non-JSON error bodies
    }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }

  return (await res.json()) as T;
}

export const api = {
  auth: {
    onboard: (data: HostOnboarding) =>
      request<HostOnboardingResponse>("/auth/onboarding", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    // Unauthenticated -- used by the "Add your voice agent's intro"
    // onboarding step.
    transcribeRegistrationIntro: (audio: Blob) =>
      uploadAudio<{ text: string }>("/auth/register-host/transcribe-intro", audio, "intro.webm"),
    me: () => request<UserOut>("/auth/me"),
    updateMe: (data: UserUpdate) => request<UserOut>("/auth/me", { method: "PATCH", body: JSON.stringify(data) }),
    uploadPhoto: (file: File) => uploadFiles<UserOut>("/auth/me/photo", [file], "file"),
    uploadBanner: (file: File) => uploadFiles<UserOut>("/auth/me/banner", [file], "file"),
  },
  properties: {
    list: () => request<PropertyOut[]>("/properties"),
    get: (id: string) => request<PropertyOut>(`/properties/${id}`),
    create: (data: PropertyCreate) =>
      request<PropertyOut>("/properties", { method: "POST", body: JSON.stringify(data) }),
    update: (id: string, data: PropertyUpdate) =>
      request<PropertyOut>(`/properties/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    remove: (id: string) => request<void>(`/properties/${id}`, { method: "DELETE" }),
    uploadPhoto: (id: string, file: File) => uploadFiles<PropertyOut>(`/properties/${id}/photos`, [file], "file"),
    syncIcal: (id: string) => request<{ created: number; updated: number }>(`/properties/${id}/sync-ical`, { method: "POST" }),
    importListings: (files: File[]) => uploadFiles<PropertyImportResult[]>("/properties/import", files),
    importAirbnbUrls: (urls: string[]) =>
      request<{ snapshot_id: string }>("/properties/import-airbnb-urls", {
        method: "POST",
        body: JSON.stringify({ urls }),
      }),
    importAirbnbUrlsStatus: (snapshotId: string) =>
      request<AirbnbUrlImportStatus>(`/properties/import-airbnb-urls/${snapshotId}`),
  },
  calls: {
    list: (params?: {
      status?: string;
      urgency?: string;
      callType?: string;
      limit?: number;
      startDate?: string;
      endDate?: string;
      includeTestCalls?: boolean;
    }) =>
      request<CallSessionOut[]>(
        `/calls${buildQuery({
          status: params?.status,
          urgency: params?.urgency,
          call_type: params?.callType,
          limit: params?.limit,
          start_date: params?.startDate,
          end_date: params?.endDate,
          include_test_calls: params?.includeTestCalls ?? false,
        })}`
      ),
    get: (id: string) => request<CallSessionDetailOut>(`/calls/${id}`),
  },
  guests: {
    list: (params?: { startDate?: string; endDate?: string }) =>
      request<GuestProfileOut[]>(
        `/guests${buildQuery({ start_date: params?.startDate, end_date: params?.endDate })}`
      ),
    get: (id: string) => request<GuestProfileOut>(`/guests/${id}`),
    detail: (id: string) => request<GuestProfileDetailOut>(`/guests/${id}/detail`),
    update: (id: string, data: GuestProfileUpdate) =>
      request<GuestProfileOut>(`/guests/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  },
  bookings: {
    list: () => request<BookingOut[]>("/bookings"),
    create: (data: BookingCreate) =>
      request<BookingOut>("/bookings", { method: "POST", body: JSON.stringify(data) }),
    checkAvailability: (data: { property_id: string; check_in: string; check_out: string; num_guests?: number }) =>
      request<{ available: boolean }>("/bookings/check-availability", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    cancel: (id: string) => request<void>(`/bookings/${id}`, { method: "DELETE" }),
  },
  pricing: {
    rules: (params?: { startDate?: string; endDate?: string }) =>
      request<PricingRuleOut[]>(
        `/pricing/rules${buildQuery({ start_date: params?.startDate, end_date: params?.endDate })}`
      ),
    createRule: (data: PricingRuleCreate) =>
      request<PricingRuleOut>("/pricing/rules", { method: "POST", body: JSON.stringify(data) }),
    removeRule: (id: string) => request<void>(`/pricing/rules/${id}`, { method: "DELETE" }),
    quote: (data: { property_id: string; check_in: string; check_out: string; num_guests: number }) =>
      request<PriceBreakdown>("/pricing/quote", { method: "POST", body: JSON.stringify(data) }),
  },
  hostDiscountRules: {
    list: () => request<HostDiscountRuleOut[]>("/host-discount-rules"),
    parse: (discountPolicyText: string) =>
      request<DiscountPolicyParseResponse>("/host-discount-rules/parse", {
        method: "POST",
        body: JSON.stringify({ discount_policy_text: discountPolicyText }),
      }),
    update: (id: string, data: HostDiscountRuleUpdate) =>
      request<HostDiscountRuleOut>(`/host-discount-rules/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    remove: (id: string) => request<void>(`/host-discount-rules/${id}`, { method: "DELETE" }),
  },
  propertyPricingRules: {
    list: () => request<PropertyPricingRuleOut[]>("/property-pricing-rules"),
    parse: (pricingPolicyText: string) =>
      request<PricingPolicyParseResponse>("/property-pricing-rules/parse", {
        method: "POST",
        body: JSON.stringify({ pricing_policy_text: pricingPolicyText }),
      }),
    update: (id: string, data: PropertyPricingRuleUpdate) =>
      request<PropertyPricingRuleOut>(`/property-pricing-rules/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    remove: (id: string) => request<void>(`/property-pricing-rules/${id}`, { method: "DELETE" }),
  },
  technicians: {
    list: () => request<TechnicianOut[]>("/technicians"),
    create: (data: TechnicianCreate) =>
      request<TechnicianOut>("/technicians", { method: "POST", body: JSON.stringify(data) }),
    remove: (id: string) => request<void>(`/technicians/${id}`, { method: "DELETE" }),
  },
  notifications: {
    list: () => request<NotificationOut[]>("/notifications"),
    markRead: (id: string) => request<NotificationOut>(`/notifications/${id}/read`, { method: "POST" }),
  },
  analytics: {
    summary: (params?: { days?: number; startDate?: string; endDate?: string; includeTestCalls?: boolean }) =>
      request<AnalyticsSummary>(
        `/analytics/summary${buildQuery({
          days: params?.startDate || params?.endDate ? undefined : (params?.days ?? 30),
          start_date: params?.startDate,
          end_date: params?.endDate,
          include_test_calls: params?.includeTestCalls ?? false,
        })}`
      ),
    timeseries: (params: {
      metric: AnalyticsTimeseriesMetric;
      startDate?: string;
      endDate?: string;
      includeTestCalls?: boolean;
    }) =>
      request<AnalyticsTimeseries>(
        `/analytics/timeseries${buildQuery({
          metric: params.metric,
          start_date: params.startDate,
          end_date: params.endDate,
          include_test_calls: params.includeTestCalls ?? false,
        })}`
      ),
  },
  leads: {
    list: (params?: { startDate?: string; endDate?: string }) =>
      request<LeadOut[]>(`/leads${buildQuery({ start_date: params?.startDate, end_date: params?.endDate })}`),
    get: (id: string) => request<LeadOut>(`/leads/${id}`),
    update: (id: string, data: LeadUpdate) =>
      request<LeadOut>(`/leads/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    serviceRequests: (params?: { includeDismissed?: boolean }) =>
      request<ServiceRequestOut[]>(
        `/leads/service-requests${buildQuery({ include_dismissed: params?.includeDismissed ?? false })}`
      ),
    dismissServiceRequests: (callSessionIds: string[]) =>
      request<{ dismissed: number }>(`/leads/service-requests/dismiss`, {
        method: "POST",
        body: JSON.stringify({ call_session_ids: callSessionIds }),
      }),
  },
  faq: {
    list: () => request<FaqEntryOut[]>("/faq"),
    create: (data: FaqEntryCreate) => request<FaqEntryOut>("/faq", { method: "POST", body: JSON.stringify(data) }),
    update: (id: string, data: FaqEntryUpdate) =>
      request<FaqEntryOut>(`/faq/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    remove: (id: string) => request<void>(`/faq/${id}`, { method: "DELETE" }),
  },
  faqGaps: {
    list: (params?: { propertyId?: string; startDate?: string; endDate?: string }) =>
      request<FaqGapOut[]>(
        `/faq/gaps${buildQuery({
          property_id: params?.propertyId,
          start_date: params?.startDate,
          end_date: params?.endDate,
        })}`
      ),
    analytics: (bucket: "week" | "month" = "week") =>
      request<FaqGapAnalytics>(`/faq/gaps/analytics${buildQuery({ bucket })}`),
    answer: (gapId: string, data: FaqGapAnswer) =>
      request<FaqEntryOut>(`/faq/gaps/${gapId}/answer`, { method: "POST", body: JSON.stringify(data) }),
    answerVoice: (gapId: string, audio: Blob, applyToProperty: boolean = false) =>
      uploadAudio<FaqEntryOut>(
        `/faq/gaps/${gapId}/answer-voice${buildQuery({ apply_to_property: applyToProperty })}`,
        audio,
        "answer.webm"
      ),
  },
  voice: {
    // Generic "dictate into this field" transcription -- see
    // src/hooks/use-dictation.ts, the shared hook every dictation mic
    // button in the dashboard goes through.
    transcribe: (audio: Blob) => uploadAudio<{ text: string }>("/voice/transcribe", audio, "dictation.webm"),
  },
};

export { API_BASE_URL };
