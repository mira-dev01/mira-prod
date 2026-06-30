import type {
  AnalyticsSummary,
  BookingCreate,
  BookingOut,
  CallSessionOut,
  FaqEntryCreate,
  FaqEntryOut,
  FaqEntryUpdate,
  GuestProfileOut,
  GuestProfileUpdate,
  LeadOut,
  LeadUpdate,
  NotificationOut,
  PriceBreakdown,
  PricingRuleCreate,
  PricingRuleOut,
  PropertyCreate,
  PropertyImportResult,
  PropertyOut,
  PropertyUpdate,
  TechnicianCreate,
  TechnicianOut,
  UserOut,
  UserUpdate,
} from "@/lib/types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
const TOKEN_KEY = "mira_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
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

async function uploadFiles<T>(path: string, files: File[]): Promise<T> {
  const token = getToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));

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

export const api = {
  auth: {
    login: (email: string, password: string) =>
      request<{ access_token: string; token_type: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      }),
    register: (email: string, password: string, name?: string, phone?: string) =>
      request<{ access_token: string; token_type: string }>("/auth/register", {
        method: "POST",
        body: JSON.stringify({ email, password, name, phone }),
      }),
    me: () => request<UserOut>("/auth/me"),
    updateMe: (data: UserUpdate) => request<UserOut>("/auth/me", { method: "PATCH", body: JSON.stringify(data) }),
  },
  properties: {
    list: () => request<PropertyOut[]>("/properties"),
    get: (id: string) => request<PropertyOut>(`/properties/${id}`),
    create: (data: PropertyCreate) =>
      request<PropertyOut>("/properties", { method: "POST", body: JSON.stringify(data) }),
    update: (id: string, data: PropertyUpdate) =>
      request<PropertyOut>(`/properties/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    remove: (id: string) => request<void>(`/properties/${id}`, { method: "DELETE" }),
    syncIcal: (id: string) => request<{ created: number; updated: number }>(`/properties/${id}/sync-ical`, { method: "POST" }),
    importListings: (files: File[]) => uploadFiles<PropertyImportResult[]>("/properties/import", files),
  },
  calls: {
    list: () => request<CallSessionOut[]>("/calls"),
    get: (id: string) => request<CallSessionOut>(`/calls/${id}`),
  },
  guests: {
    list: () => request<GuestProfileOut[]>("/guests"),
    get: (id: string) => request<GuestProfileOut>(`/guests/${id}`),
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
    rules: () => request<PricingRuleOut[]>("/pricing/rules"),
    createRule: (data: PricingRuleCreate) =>
      request<PricingRuleOut>("/pricing/rules", { method: "POST", body: JSON.stringify(data) }),
    removeRule: (id: string) => request<void>(`/pricing/rules/${id}`, { method: "DELETE" }),
    quote: (data: { property_id: string; check_in: string; check_out: string; num_guests: number }) =>
      request<PriceBreakdown>("/pricing/quote", { method: "POST", body: JSON.stringify(data) }),
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
    summary: (days = 30, includeTestCalls = false) =>
      request<AnalyticsSummary>(`/analytics/summary?days=${days}&include_test_calls=${includeTestCalls}`),
  },
  leads: {
    list: () => request<LeadOut[]>("/leads"),
    get: (id: string) => request<LeadOut>(`/leads/${id}`),
    update: (id: string, data: LeadUpdate) =>
      request<LeadOut>(`/leads/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  },
  faq: {
    list: () => request<FaqEntryOut[]>("/faq"),
    create: (data: FaqEntryCreate) => request<FaqEntryOut>("/faq", { method: "POST", body: JSON.stringify(data) }),
    update: (id: string, data: FaqEntryUpdate) =>
      request<FaqEntryOut>(`/faq/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    remove: (id: string) => request<void>(`/faq/${id}`, { method: "DELETE" }),
  },
};

export { API_BASE_URL };
