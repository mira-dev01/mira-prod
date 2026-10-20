// Client for the internal /admin panel API (backend app/api/v1/admin*.py).
// Deliberately separate from lib/api.ts: admins don't use Clerk. The admin
// session is a short-lived token from the email one-time-code login, kept in
// localStorage and sent as a Bearer token on every /admin/* request.

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
const TOKEN_KEY = "mira_admin_session";

type StoredSession = { token: string; email: string; expires_at: string };

export function getAdminSession(): StoredSession | null {
  try {
    const raw = window.localStorage.getItem(TOKEN_KEY);
    if (!raw) return null;
    const session = JSON.parse(raw) as StoredSession;
    if (new Date(session.expires_at).getTime() <= Date.now()) {
      window.localStorage.removeItem(TOKEN_KEY);
      return null;
    }
    return session;
  } catch {
    return null;
  }
}

export function setAdminSession(session: StoredSession | null): void {
  try {
    if (session) window.localStorage.setItem(TOKEN_KEY, JSON.stringify(session));
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    // storage unavailable (private mode) -- the session just won't persist
  }
}

export class AdminApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  const session = getAdminSession();
  if (session) headers.set("Authorization", `Bearer ${session.token}`);

  const res = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // non-JSON error body
    }
    if (res.status === 401 || res.status === 403) setAdminSession(null);
    throw new AdminApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return (await res.json()) as T;
}

export type AdminFilters = { startDate: string; endDate: string; includeTestCalls: boolean };

function qs(f: AdminFilters): string {
  const p = new URLSearchParams({ start_date: f.startDate, end_date: f.endDate });
  if (f.includeTestCalls) p.set("include_test_calls", "true");
  return `?${p.toString()}`;
}

// ── payload types ───────────────────────────────────────────────────────────

export type Pct = { p50: number | null; p90?: number | null; p95?: number | null };
export type HistogramBucket = { from: number; to: number; count: number };

export type Decision = {
  key: string;
  label: string;
  verdict: "enable" | "hold" | "insufficient_data";
  metric: string;
  value: number | null;
  rule: string;
};

export type FunnelStep = {
  key: string;
  label: string;
  count: number;
  base?: number;
  telemetry_only?: boolean;
  rate_of_previous: number | null;
  rate_of_calls: number | null;
};

export type OverviewData = {
  kpis: {
    calls: number;
    answered: number;
    engaged_rate: number | null;
    leads: number;
    escalated: number;
    response_p50_s: number | null;
    phantom_rate: number | null;
    clarifications_per_call: number | null;
    cost_inr: number;
    cost_per_answered_call_inr: number | null;
  };
  funnel: FunnelStep[];
  daily_cost: { date: string; cost_inr: number }[];
  decisions: Decision[];
  coverage: { calls: number; calls_with_call_metrics: number; calls_with_audio_telemetry: number };
};

export type AudioData = {
  coverage: {
    calls_in_range: number;
    answered_calls: number;
    calls_with_audio_telemetry: number;
    segment_sample_calls: number;
    segment_sample_size: number;
    min_calls_for_decision: number;
  };
  decisions: Decision[];
  interruptions: {
    barge_ins_total: number;
    barge_ins_per_call: number | null;
    phantom_barge_ins: number;
    phantom_rate: number | null;
    by_source: { sarvam_stt: number; downstream_turn_controller: number };
    phantom_by_source_sample: Record<string, number>;
    dual_source: number;
    transcript_triggered: number;
    outcomes: Record<string, number>;
    calls_with_sarvam_vad_events: number;
    sarvam_vad_share: number | null;
  };
  segments: { total: number; short: number; short_share: number | null; bot_overlap: number; bot_overlap_share: number | null };
  shadow: {
    echo_drop: number;
    echo_per_100_segments: number | null;
    short_drop: number;
    short_drop_by_class: Record<string, number>;
    level_drop: number;
    level_clarify: number;
    any_action_share: number | null;
  };
  guard: { triggers: number; trigger_rate: number | null; short_triggers: number; short_share_of_triggers: number | null };
  levels: {
    snr_db: { p10: number | null; p50: number | null; p90: number | null };
    noise_floor_dbfs_p50: number | null;
    lang_prob_histogram: HistogramBucket[];
    relative_db_histogram: HistogramBucket[];
  };
  stt_latency_s: { p50: number | null; p95: number | null };
  top_short_texts: {
    text: string;
    count: number;
    token_class: string | null;
    while_bot_speaking: number;
    guard_triggered: number;
  }[];
  overhead_ms: { p50: number | null; max: number | null };
};

export type ConversationsData = {
  coverage: { calls: number; calls_with_call_metrics: number; calls_with_audio_telemetry: number };
  funnel: FunnelStep[];
  outcomes: {
    escalated: number;
    transferred_to_host: number;
    by_call_type: { call_type: string; count: number }[];
    by_end: { end: string; count: number }[];
    duration_s: { avg: number | null; p50: number | null; p90: number | null };
    short_calls_under_20s: number;
    short_call_share: number | null;
  };
  understanding: {
    guard_firings: { rule: string; count: number; per_100_calls: number | null }[];
    clarifications_total: number;
    clarifications_per_call: number | null;
    calls_with_clarification_share: number | null;
  };
  language: {
    segments_by_language: { language: string; segments: number }[];
    calls_by_dominant_language: { language: string; calls: number }[];
    avg_switches_per_call: number | null;
    calls_with_switch_share: number | null;
  };
};

export type PerformanceData = {
  coverage: { calls: number; calls_with_call_metrics: number };
  latency: {
    response_s: { p50: number | null; p90: number | null; p95: number | null; samples: number };
    turn_detection_wait_s: number;
    llm_ttfb_s: { p50: number | null; p95: number | null };
    tts_ttfb_s: { p50: number | null; p95: number | null };
    response_histogram: HistogramBucket[];
  };
  tools: { name: string; calls: number; errors: number; error_rate: number | null; p50_s: number | null; p95_s: number | null }[];
  llm: {
    models: {
      provider: string;
      model: string | null;
      completions: number;
      prompt_tokens: number;
      completion_tokens: number;
      share: number | null;
    }[];
    primary_model: string;
    fallback_share: number | null;
    pipeline_errors: number;
    calls_with_errors_share: number | null;
    system_failures: number;
  };
};

export type LeadsData = {
  coverage: { calls: number; calls_with_call_metrics: number };
  lead_safety: {
    leads_from_calls: number;
    calls_with_pricing_engagement: number;
    engaged_without_lead: number;
    engaged_without_lead_call_ids: string[];
    leads_via_update_lead: number;
    leads_via_safety_net: number;
    safety_net_share: number | null;
    incomplete_leads: number;
  };
  busy_recovery: {
    busy_rejections: number;
    recovery_leads: number;
    recovery_rate: number | null;
    availability_status: { status: string; count: number }[];
    progressed: number;
  };
  escalations: {
    total: number;
    by_urgency: { urgency: string; count: number }[];
    by_status: { status: string; count: number }[];
    responded: number;
    response_minutes: { p50: number | null; p90: number | null };
    unresponded_over_1h: number;
  };
};

type GroupStats = {
  calls: number;
  answered: number;
  busy_rejected: number;
  leads: number;
  escalations: number;
  avg_duration_s: number | null;
  guard_firings_per_call: number | null;
  response_p50_s: number | null;
  last_call_at: string | null;
};
export type HostsData = {
  hosts: (GroupStats & {
    user_id: string | null;
    name: string;
    email: string | null;
    cost_inr: number;
    cost_per_answered_inr: number | null;
  })[];
  properties: (GroupStats & { property_id: string; name: string; city: string | null })[];
};

export type UsageRow = {
  account: string;
  service: string;
  unit: string;
  model: string | null;
  quantity: number;
  unit_price: number;
  currency: string;
  cost: number;
  cost_inr: number;
  priced: boolean;
};
export type UsageData = {
  usd_inr: number;
  rows: UsageRow[];
  by_account: { account: string; label: string; currency: string; cost: number; cost_inr: number; unpriced_units: string[] }[];
  totals: { cost_inr: number; answered_phone_calls: number; cost_per_answered_call_inr: number | null };
  daily: { date: string; cost_inr: number }[];
};

export type BalanceCard = {
  account: string;
  label: string;
  kind: "prepaid" | "postpaid" | "quota" | "live";
  status: "ok" | "not_set" | "not_configured" | "error";
  level?: "ok" | "low" | "critical" | "unknown";
  currency?: string | null;
  balance?: number | null;
  used?: number | null;
  limit?: number | null;
  unit?: string | null;
  detail?: string | null;
  prepaid_amount?: number;
  prepaid_set_at?: string;
  spent_since?: number;
  remaining_share?: number | null;
  burn_per_day?: number;
  days_left?: number | null;
  fetched_at?: string;
};

export type ServiceSetting = {
  account: string;
  label: string;
  kind: string;
  currency: string;
  unit_prices: Record<string, number>;
  default_unit_prices: Record<string, number>;
  prepaid_amount: number | null;
  prepaid_set_at: string | null;
  note: string | null;
  updated_by: string | null;
};

export type ServiceSettingUpdate = {
  prepaid_amount?: number;
  clear_prepaid?: boolean;
  unit_prices?: Record<string, number | null>;
  currency?: "INR" | "USD";
  note?: string;
};

export type HealthState = "up" | "degraded" | "down" | "unknown" | "not_configured";

export type HealthService = {
  key: string;
  label: string;
  group: string;
  description: string;
  critical: boolean;
  state: HealthState;
  not_configured_reason?: string | null;
  since: string | null;
  last_ok_at: string | null;
  last_activity_at: string | null;
  stale: boolean;
  last_error: {
    at: string;
    message: string;
    kind: string | null;
    op: string | null;
    outcome: string;
    call_session_id?: string | null;
    host_id?: string | null;
  } | null;
  window: { requests: number; ok: number; warnings: number; errors: number; error_rate: number | null; p95_ms: number | null };
  probe: { status: string | null; at: string | null; latency_ms: number | null; consecutive_failures: number; error: string | null } | null;
  detail: Record<string, unknown>;
  open_incident_id: string | null;
  availability_24h: number | null;
  incidents_24h: number;
};

export type HealthIncident = {
  id: string;
  service: string;
  label: string;
  group: string;
  severity: "degraded" | "down";
  state: HealthState;
  opened_at: string;
  resolved_at: string | null;
  duration_s: number;
  down_seconds: number;
  first_error: string | null;
  last_error: string | null;
  error_count: number;
  sample_call_session_id: string | null;
  alerts_sent: number;
  last_alert_at: string | null;
};

export type HealthSnapshot = {
  environment: string;
  generated_at: string;
  version: number;
  alerts_enabled: boolean;
  alert_recipients: string[];
  overall: "up" | "degraded" | "down";
  counts: Partial<Record<HealthState, number>>;
  groups: { key: string; label: string; services: HealthService[] }[];
  incidents: HealthIncident[];
};

/** Server-sent events over fetch (EventSource can't send the Bearer header).
 * Resolves when the stream ends (server closes it every ~15 min, or network
 * drop) -- the caller reconnects. Rejects with AdminApiError on 401/403. */
export async function streamHealth(onSnapshot: (s: HealthSnapshot) => void, signal: AbortSignal): Promise<void> {
  const headers = new Headers({ Accept: "text/event-stream" });
  const session = getAdminSession();
  if (session) headers.set("Authorization", `Bearer ${session.token}`);
  const res = await fetch(`${API_BASE_URL}/admin/health/stream`, { headers, signal, cache: "no-store" });
  if (!res.ok || !res.body) {
    if (res.status === 401 || res.status === 403) setAdminSession(null);
    throw new AdminApiError(res.status, res.statusText || "Health stream failed");
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const chunk = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = chunk
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (data) {
        try {
          onSnapshot(JSON.parse(data) as HealthSnapshot);
        } catch {
          // malformed frame -- skip it, the next tick replaces it anyway
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

export const adminApi = {
  auth: {
    requestCode: (email: string) =>
      request<{ status: string; detail: string }>("/admin/auth/request-code", {
        method: "POST",
        body: JSON.stringify({ email }),
      }),
    verifyCode: (email: string, code: string) =>
      request<StoredSession>("/admin/auth/verify-code", { method: "POST", body: JSON.stringify({ email, code }) }),
    me: () => request<{ email: string }>("/admin/auth/me"),
  },
  overview: (f: AdminFilters) => request<OverviewData>(`/admin/overview${qs(f)}`),
  audio: (f: AdminFilters) => request<AudioData>(`/admin/audio${qs(f)}`),
  conversations: (f: AdminFilters) => request<ConversationsData>(`/admin/conversations${qs(f)}`),
  performance: (f: AdminFilters) => request<PerformanceData>(`/admin/performance${qs(f)}`),
  leads: (f: AdminFilters) => request<LeadsData>(`/admin/leads${qs(f)}`),
  hosts: (f: AdminFilters) => request<HostsData>(`/admin/hosts${qs(f)}`),
  usage: (f: AdminFilters) => request<UsageData>(`/admin/usage${qs(f)}`),
  health: () => request<HealthSnapshot>("/admin/health"),
  sendHealthDigest: () =>
    request<{ status: string; recipients?: string[] }>("/admin/health/digest", { method: "POST" }),
  balances: (refresh = false) => request<BalanceCard[]>(`/admin/balances${refresh ? "?refresh=true" : ""}`),
  settings: {
    list: () => request<ServiceSetting[]>("/admin/settings/services"),
    update: (account: string, body: ServiceSettingUpdate) =>
      request<ServiceSetting[]>(`/admin/settings/services/${account}`, { method: "PUT", body: JSON.stringify(body) }),
  },
};
