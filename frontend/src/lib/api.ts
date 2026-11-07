import type {
  AirbnbUrlImportStatus,
  AnalyticsEntryInput,
  AnalyticsLayout,
  AnalyticsDashboard,
  AnalyticsSummary,
  AnalyticsTimeseries,
  AnalyticsTimeseriesMetric,
  BookingCreate,
  BookingKind,
  StayScope,
  BookingOut,
  BookingReconciliation,
  CallHoursStatus,
  CallSessionDetailOut,
  CallSessionOut,
  CapabilityUpdate,
  AgentConfig,
  AgentConfigOut,
  BillingTemplateName,
  BillingView,
  PaymentRequestOut,
  PolicyOut,
  PolicyOverview,
  PolicyUpload,
  TicketOut,
  TicketStatus,
  AgentConfigUpdate,
  FaqEntryCreate,
  FaqEntryOut,
  FaqEntryUpdate,
  FaqScope,
  FaqGapAnalytics,
  FaqGapAnswer,
  FaqGapOut,
  GuestProfileDetailOut,
  GuestProfileOut,
  GuestProfileUpdate,
  HostCapabilities,
  HostOnboarding,
  HostOnboardingResponse,
  LeadOut,
  LeadUpdate,
  NavigationPreferences,
  NavigationPreferencesUpdate,
  NegotiationPolicyParseResponse,
  NegotiationRuleOut,
  NegotiationRuleUpdate,
  NotificationPreferencesUpdate,
  NotificationSettings,
  NotificationOut,
  OverviewData,
  OverviewLayout,
  OverviewLayoutUpdate,
  ObjectionInsights,
  OnboardingProgressUpdate,
  OnboardingState,
  ServiceRequestOut,
  PriceBreakdown,
  PriceChoice,
  PricingRuleCreate,
  PricingRuleOut,
  PropertyCreate,
  PropertyImportResult,
  PropertyOut,
  PropertyUpdate,
  ReconciliationQueue,
  RecoveryAnalytics,
  TechnicianCreate,
  TechnicianOut,
  TransferNumberGroup,
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

function buildQuery(params: Record<string, string | number | boolean | string[] | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) value.forEach((item) => search.append(key, item));
    else if (value !== undefined) search.set(key, String(value));
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
    // The account's live call-routing decision right now, computed by the
    // same resolver the Exotel webhook uses on a real call -- see
    // CallHoursStatus in backend/app/schemas/user.py.
    callHoursStatus: () => request<CallHoursStatus>("/auth/me/call-hours-status"),
  },
  // Capability management -- always scoped server-side to the signed-in
  // host (backend app/api/v1/capabilities.py).
  capabilities: {
    list: () => request<HostCapabilities>("/capabilities"),
    update: (id: string, data: CapabilityUpdate) =>
      request<HostCapabilities>(`/capabilities/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  },
  // Server-persisted onboarding progress (backend app/api/v1/onboarding.py).
  // Business-profile fields go through auth.updateMe, not here.
  onboarding: {
    get: () => request<OnboardingState>("/onboarding"),
    save: (data: OnboardingProgressUpdate) =>
      request<OnboardingState>("/onboarding", { method: "PUT", body: JSON.stringify(data) }),
    startFirstPropertyImport: (data: { airbnb_url: string; ical_url?: string | null; live_pricing?: boolean }) =>
      request<OnboardingState>("/onboarding/first-property", { method: "POST", body: JSON.stringify(data) }),
    complete: () => request<OnboardingState>("/onboarding/complete", { method: "POST" }),
  },
  // Dashboard layout (backend app/api/v1/preferences.py) -- the signed-in
  // user's own navigation order/visibility and Overview widget layout.
  // Stored separately from capability activation above.
  preferences: {
    navigation: () => request<NavigationPreferences>("/preferences/navigation"),
    saveNavigation: (data: NavigationPreferencesUpdate) =>
      request<NavigationPreferences>("/preferences/navigation", { method: "PUT", body: JSON.stringify(data) }),
    resetNavigation: () => request<NavigationPreferences>("/preferences/navigation", { method: "DELETE" }),
    overviewWidgets: () => request<OverviewLayout>("/preferences/overview-widgets"),
    saveOverviewWidgets: (data: OverviewLayoutUpdate) =>
      request<OverviewLayout>("/preferences/overview-widgets", { method: "PUT", body: JSON.stringify(data) }),
    resetOverviewWidgets: () => request<OverviewLayout>("/preferences/overview-widgets", { method: "DELETE" }),
    analyticsWidgets: () => request<AnalyticsLayout>("/preferences/analytics-widgets"),
    saveAnalyticsWidgets: (data: { entries: AnalyticsEntryInput[]; expected_revision: number }) =>
      request<AnalyticsLayout>("/preferences/analytics-widgets", { method: "PUT", body: JSON.stringify(data) }),
    resetAnalyticsWidgets: () => request<AnalyticsLayout>("/preferences/analytics-widgets", { method: "DELETE" }),
  },
  // Settings > Your account > Notifications & escalations.
  notificationSettings: {
    get: () => request<NotificationSettings>("/notification-settings"),
    update: (data: NotificationPreferencesUpdate) =>
      request<NotificationSettings>("/notification-settings", { method: "PATCH", body: JSON.stringify(data) }),
    preview: (data: { subject?: string | null; body?: string | null }) =>
      request<{ subject: string; html: string }>("/notification-settings/preview", {
        method: "POST",
        body: JSON.stringify(data),
      }),
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
    // Host transfer numbers per group of properties (replaces every
    // assignment); properties left out use the account number.
    setTransferNumbers: (groups: TransferNumberGroup[]) =>
      request<PropertyOut[]>("/properties/transfer-numbers", { method: "PUT", body: JSON.stringify({ groups }) }),
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
    reconciliationQueue: (kind?: "price" | "attribution") =>
      request<ReconciliationQueue>(`/bookings/reconciliation${buildQuery({ kind })}`),
    reconciliation: (id: string) => request<BookingReconciliation>(`/bookings/${id}/reconciliation`),
    confirmPrice: (id: string, data: { choice: PriceChoice; final_booking_price?: number | null }) =>
      request<BookingReconciliation>(`/bookings/${id}/price`, { method: "PATCH", body: JSON.stringify(data) }),
    decideAttribution: (id: string, isMiraMatch: boolean) =>
      request<BookingReconciliation>(`/bookings/${id}/attribution`, {
        method: "PATCH",
        body: JSON.stringify({ is_mira_match: isMiraMatch }),
      }),
    updateKind: (id: string, kind: BookingKind) =>
      request<BookingReconciliation>(`/bookings/${id}/kind`, { method: "PATCH", body: JSON.stringify({ kind }) }),
    updateScope: (id: string, data: { stay_scope: StayScope; rooms_booked?: number | null }) =>
      request<BookingReconciliation>(`/bookings/${id}/scope`, { method: "PATCH", body: JSON.stringify(data) }),
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
  negotiationRules: {
    list: () => request<NegotiationRuleOut[]>("/negotiation-rules"),
    parse: (policyText: string) =>
      request<NegotiationPolicyParseResponse>("/negotiation-rules/parse", {
        method: "POST",
        body: JSON.stringify({ policy_text: policyText }),
      }),
    update: (id: string, data: NegotiationRuleUpdate) =>
      request<NegotiationRuleOut>(`/negotiation-rules/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    remove: (id: string) => request<void>(`/negotiation-rules/${id}`, { method: "DELETE" }),
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
    recovery: (params?: { days?: number; startDate?: string; endDate?: string }) =>
      request<RecoveryAnalytics>(
        `/analytics/recovery${buildQuery({
          days: params?.startDate || params?.endDate ? undefined : (params?.days ?? 30),
          start_date: params?.startDate,
          end_date: params?.endDate,
        })}`
      ),
    overview: (params: { startDate?: string; endDate?: string; includeTestCalls?: boolean }) =>
      request<OverviewData>(
        `/analytics/overview${buildQuery({
          start_date: params.startDate,
          end_date: params.endDate,
          include_test_calls: params.includeTestCalls ?? false,
        })}`
      ),
    dashboard: (params: { startDate?: string; endDate?: string; propertyId?: string; includeTestCalls?: boolean }) =>
      request<AnalyticsDashboard>(
        `/analytics/dashboard${buildQuery({
          start_date: params.startDate,
          end_date: params.endDate,
          property_id: params.propertyId,
          include_test_calls: params.includeTestCalls ?? false,
        })}`
      ),
    objectionInsights: (params?: { startDate?: string; endDate?: string }) =>
      request<ObjectionInsights>(
        `/analytics/objection-insights${buildQuery({
          start_date: params?.startDate,
          end_date: params?.endDate,
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
    remove: (id: string, opts?: { group?: boolean }) =>
      request<void>(`/faq/${id}${buildQuery({ group: opts?.group })}`, { method: "DELETE" }),
    setScope: (id: string, scope: FaqScope) =>
      request<FaqEntryOut[]>(`/faq/${id}/scope`, { method: "PUT", body: JSON.stringify(scope) }),
  },
  tickets: {
    list: (state: "open" | "resolved" | "all" = "open") => request<TicketOut[]>(`/tickets${buildQuery({ state })}`),
    setStatus: (id: string, status: TicketStatus) =>
      request<TicketOut>(`/tickets/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
  },
  policies: {
    overview: () => request<PolicyOverview>("/policies"),
    create: (data: { topic: string; text: string; title?: string; property_id?: string | null }) =>
      request<PolicyOut>("/policies", { method: "POST", body: JSON.stringify(data) }),
    /** One policy for the whole portfolio (no propertyIds) or for each chosen property. */
    createScoped: (data: { topic: string; text: string; title?: string; property_ids?: string[] }) =>
      request<PolicyOut[]>("/policies/scoped", { method: "POST", body: JSON.stringify(data) }),
    update: (id: string, data: { text?: string; title?: string; topic?: string }) =>
      request<PolicyOut>(`/policies/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    remove: (id: string) => request<void>(`/policies/${id}`, { method: "DELETE" }),
    /** Omit `scope` to keep the properties the document named; otherwise apply to all, or only the chosen ones. */
    approve: (id: string, scope?: { apply_to_all?: boolean; property_ids?: string[] }) =>
      request<{ policy: PolicyOut; replaced: boolean }>(`/policies/${id}/approve`, {
        method: "POST",
        ...(scope ? { body: JSON.stringify(scope) } : {}),
      }),
    approveAll: (documentId?: string) =>
      request<{ approved: number }>("/policies/approve-all", {
        method: "POST",
        body: JSON.stringify({ document_id: documentId ?? null }),
      }),
    upload: (file: File) => uploadFiles<PolicyUpload>("/policies/documents", [file], "file"),
  },
  agentConfig: {
    get: () => request<AgentConfigOut>("/agent-config"),
    update: (data: AgentConfigUpdate) =>
      request<AgentConfigOut>("/agent-config", { method: "PUT", body: JSON.stringify(data) }),
    setPaymentOverride: (propertyId: string, data: Partial<AgentConfig["payment"]>) =>
      request<AgentConfigOut>(`/agent-config/properties/${propertyId}/payment`, { method: "PUT", body: JSON.stringify(data) }),
    clearPaymentOverride: (propertyId: string) =>
      request<AgentConfigOut>(`/agent-config/properties/${propertyId}/payment`, { method: "DELETE" }),
  },
  billing: {
    get: () => request<BillingView>("/billing"),
    connectRazorpay: (keyId: string, keySecret: string) =>
      request<BillingView>("/billing/razorpay", { method: "PUT", body: JSON.stringify({ key_id: keyId, key_secret: keySecret }) }),
    disconnectRazorpay: () => request<BillingView>("/billing/razorpay", { method: "DELETE" }),
    saveTemplate: (name: BillingTemplateName, text: string) =>
      request<BillingView>(`/billing/templates/${name}`, { method: "PUT", body: JSON.stringify({ text }) }),
    approveTemplate: (name: BillingTemplateName) => request<BillingView>(`/billing/templates/${name}/approve`, { method: "POST" }),
    resetTemplate: (name: BillingTemplateName) => request<BillingView>(`/billing/templates/${name}`, { method: "DELETE" }),
    setNotifyHost: (enabled: boolean) =>
      request<BillingView>("/billing/notify-host", { method: "PUT", body: JSON.stringify({ enabled }) }),
  },
  payments: {
    list: () => request<PaymentRequestOut[]>("/payments"),
    cancel: (id: string) => request<{ cancelled: boolean }>(`/payments/${id}/cancel`, { method: "POST" }),
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
    answerVoice: (gapId: string, audio: Blob, scope: FaqScope, question?: string) =>
      uploadAudio<FaqEntryOut>(
        `/faq/gaps/${gapId}/answer-voice${buildQuery({
          apply_to_all: scope.apply_to_all,
          property_ids: scope.property_ids,
          question,
        })}`,
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
