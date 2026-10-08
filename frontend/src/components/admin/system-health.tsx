"use client";

// Live service health for the /admin home page. Fed by the backend's SSE
// stream (app/api/v1/admin.py /admin/health/stream, ~10s ticks); falls back
// to reconnecting with backoff, and the first paint comes from a plain GET.

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, CheckCircle2, CircleSlash, Copy, Mail, Radio, XCircle } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Section, fmtPct } from "@/components/admin/admin-ui";
import {
  AdminApiError,
  adminApi,
  streamHealth,
  type HealthIncident,
  type HealthService,
  type HealthSnapshot,
  type HealthState,
} from "@/lib/admin-api";
import { cn } from "@/lib/utils";

// ── state styling ───────────────────────────────────────────────────────────

const STATE: Record<HealthState, { label: string; color: string; bg: string }> = {
  up: { label: "Up", color: "var(--status-live)", bg: "var(--status-live-bg)" },
  degraded: { label: "Degraded", color: "var(--status-pending)", bg: "var(--status-pending-bg)" },
  down: { label: "Down", color: "var(--destructive)", bg: "color-mix(in oklch, var(--destructive) 12%, transparent)" },
  unknown: { label: "No data yet", color: "var(--priority-low)", bg: "var(--priority-low-bg)" },
  not_configured: { label: "Not configured", color: "var(--priority-low)", bg: "var(--priority-low-bg)" },
};

const CREDIT_LABELS: Partial<Record<HealthState, string>> = { up: "Healthy", degraded: "Low", down: "Recharge" };

function stateLabel(service: Pick<HealthService, "state" | "group">) {
  return (service.group === "credits" && CREDIT_LABELS[service.state]) || STATE[service.state].label;
}

function StatePill({ service }: { service: Pick<HealthService, "state" | "group"> }) {
  const s = STATE[service.state];
  return (
    <span
      className="inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium"
      style={{ color: s.color, backgroundColor: s.bg }}
    >
      {stateLabel(service)}
    </span>
  );
}

function Dot({ state, pulse }: { state: HealthState; pulse?: boolean }) {
  return (
    <span className="relative flex size-2.5 shrink-0">
      {pulse && <span className="absolute inline-flex size-full animate-ping rounded-full opacity-60" style={{ backgroundColor: STATE[state].color }} />}
      <span className="relative inline-flex size-2.5 rounded-full" style={{ backgroundColor: STATE[state].color }} />
    </span>
  );
}

// ── time helpers ────────────────────────────────────────────────────────────

function useNow(intervalMs = 5000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

function ago(iso: string | null | undefined, now: number): string {
  if (!iso) return "—";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function duration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

function clock(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

// ── live feed ───────────────────────────────────────────────────────────────

function useHealthFeed() {
  const router = useRouter();
  const [snapshot, setSnapshot] = useState<HealthSnapshot | null>(null);
  const [live, setLive] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let stopped = false;

    const onAuthError = (err: unknown) => {
      if (err instanceof AdminApiError && (err.status === 401 || err.status === 403)) {
        stopped = true;
        router.replace("/admin/login");
        return true;
      }
      return false;
    };

    adminApi
      .health()
      .then((s) => !stopped && setSnapshot(s))
      .catch((err) => {
        if (!onAuthError(err) && !stopped) setError(err instanceof Error ? err : new Error(String(err)));
      });

    (async () => {
      let backoff = 2000;
      while (!stopped) {
        try {
          await streamHealth((s) => {
            setSnapshot(s);
            setLive(true);
            setError(null);
            backoff = 2000;
          }, controller.signal);
        } catch (err) {
          if (stopped || controller.signal.aborted || onAuthError(err)) return;
        }
        setLive(false);
        if (stopped) return;
        await new Promise((r) => setTimeout(r, backoff));
        backoff = Math.min(backoff * 2, 30000);
      }
    })();

    return () => {
      stopped = true;
      controller.abort();
    };
  }, [router]);

  return { snapshot, live, error };
}

// ── 24h strip ───────────────────────────────────────────────────────────────

const SEVERITY: Record<string, number> = { degraded: 1, down: 2 };

function hourlyBuckets(incidents: HealthIncident[], now: number): (null | "degraded" | "down")[] {
  const hour = 3600_000;
  const start = now - 24 * hour;
  return Array.from({ length: 24 }, (_, i) => {
    const from = start + i * hour;
    const to = from + hour;
    let worst: null | "degraded" | "down" = null;
    for (const inc of incidents) {
      const opened = new Date(inc.opened_at).getTime();
      const resolved = inc.resolved_at ? new Date(inc.resolved_at).getTime() : now;
      if (opened < to && resolved > from && (!worst || SEVERITY[inc.severity] > SEVERITY[worst])) worst = inc.severity;
    }
    return worst;
  });
}

function DayStrip({ incidents, now }: { incidents: HealthIncident[]; now: number }) {
  const buckets = hourlyBuckets(incidents, now);
  return (
    <div className="flex h-1.5 gap-px" aria-label="Last 24 hours">
      {buckets.map((b, i) => (
        <span
          key={i}
          className="flex-1 rounded-[1px]"
          style={{ backgroundColor: b ? STATE[b].color : "color-mix(in oklch, var(--status-live) 35%, transparent)" }}
        />
      ))}
    </div>
  );
}

// ── service row ─────────────────────────────────────────────────────────────

function metricLine(service: HealthService, now: number): string {
  if (service.state === "not_configured") return service.not_configured_reason ?? "Credentials not set";
  if (service.group === "credits") return (service.detail.summary as string) || "—";
  const parts: string[] = [];
  const w = service.window;
  if (w.requests > 0) {
    parts.push(`${w.requests} in 5m`);
    if (w.error_rate != null) parts.push(`${fmtPct(w.error_rate)} errors`);
    if (w.p95_ms != null) parts.push(`p95 ${w.p95_ms < 1000 ? `${Math.round(w.p95_ms)}ms` : `${(w.p95_ms / 1000).toFixed(1)}s`}`);
  } else if (service.probe?.latency_ms != null) {
    parts.push(`check ${Math.round(service.probe.latency_ms)}ms`);
  }
  if (service.last_activity_at) parts.push(`seen ${ago(service.last_activity_at, now)}`);
  else parts.push("no traffic yet");
  return parts.join(" · ");
}

function ServiceRow({
  service,
  incidents,
  now,
  onOpen,
}: {
  service: HealthService;
  incidents: HealthIncident[];
  now: number;
  onOpen: () => void;
}) {
  const failing = service.state === "down" || service.state === "degraded";
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "w-full space-y-1.5 rounded-lg border px-3 py-2.5 text-left transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        service.state === "not_configured" && "opacity-60"
      )}
      style={failing ? { borderColor: STATE[service.state].color } : undefined}
    >
      <div className="flex items-center gap-2">
        <Dot state={service.state} pulse={service.state === "down"} />
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{service.label}</span>
        {service.critical && service.group !== "credits" && (
          <span className="hidden shrink-0 rounded border px-1.5 text-[10px] uppercase tracking-wide text-muted-foreground sm:inline">
            call path
          </span>
        )}
        <StatePill service={service} />
      </div>
      <p className="truncate text-xs text-muted-foreground tabular-nums">{metricLine(service, now)}</p>
      {failing && service.last_error && (
        <p className="line-clamp-2 text-xs" style={{ color: STATE[service.state].color }}>
          {service.stale ? "Last known: " : ""}
          {service.last_error.message}
        </p>
      )}
      {service.state !== "not_configured" && service.group !== "credits" && <DayStrip incidents={incidents} now={now} />}
    </button>
  );
}

// ── detail dialog ───────────────────────────────────────────────────────────

function copy(text: string) {
  navigator.clipboard?.writeText(text).then(
    () => toast.success("Copied"),
    () => toast.error("Couldn't copy")
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[110px_1fr] gap-2 py-1 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="min-w-0 break-words">{children}</span>
    </div>
  );
}

function DetailExtras({ detail }: { detail: Record<string, unknown> }) {
  const models = detail.models as { model: string; ok: boolean; latency_s: number | null; error: string | null }[] | undefined;
  const jobs = detail.jobs as Record<string, { ok: boolean; at: string; error: string | null }> | undefined;
  if (models?.length) {
    return (
      <div className="space-y-1">
        <p className="text-micro text-muted-foreground">Models (fallback order)</p>
        {models.map((m) => (
          <div key={m.model} className="flex items-start gap-2 text-xs">
            <Dot state={m.ok ? "up" : "down"} />
            <span className="font-mono">{m.model}</span>
            <span className="ml-auto shrink-0 text-muted-foreground">{m.ok ? `${m.latency_s?.toFixed(2)}s` : "failing"}</span>
          </div>
        ))}
      </div>
    );
  }
  if (jobs && Object.keys(jobs).length) {
    return (
      <div className="space-y-1">
        <p className="text-micro text-muted-foreground">Jobs (last run)</p>
        {Object.entries(jobs).map(([id, j]) => (
          <div key={id} className="flex items-start gap-2 text-xs">
            <Dot state={j.ok ? "up" : "degraded"} />
            <span className="font-mono">{id}</span>
            <span className="ml-auto shrink-0 text-muted-foreground">{j.ok ? clock(j.at) : j.error}</span>
          </div>
        ))}
      </div>
    );
  }
  return null;
}

function ServiceDialog({
  service,
  incidents,
  onClose,
  now,
}: {
  service: HealthService | null;
  incidents: HealthIncident[];
  onClose: () => void;
  now: number;
}) {
  return (
    <Dialog open={service != null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        {service && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Dot state={service.state} /> {service.label} <StatePill service={service} />
              </DialogTitle>
              <DialogDescription>{service.description}</DialogDescription>
            </DialogHeader>
            <div className="max-h-[60vh] space-y-3 overflow-y-auto">
              <div className="divide-y">
                <Field label="In this state">{service.since ? `since ${clock(service.since)} (${ago(service.since, now)})` : "—"}</Field>
                <Field label="Last success">{ago(service.last_ok_at, now)}</Field>
                {service.group === "credits" ? (
                  <Field label="Balance">{(service.detail.summary as string) || "—"}</Field>
                ) : (
                  <Field label="Last 5 min">
                    {service.window.requests
                      ? `${service.window.ok} ok · ${service.window.errors} failed${service.window.warnings ? ` · ${service.window.warnings} warnings` : ""}${service.window.p95_ms != null ? ` · p95 ${Math.round(service.window.p95_ms)}ms` : ""}`
                      : "No real traffic"}
                  </Field>
                )}
                {service.probe && (
                  <Field label="Health check">
                    {service.probe.status} · {ago(service.probe.at, now)}
                    {service.probe.latency_ms != null && ` · ${Math.round(service.probe.latency_ms)}ms`}
                    {service.probe.consecutive_failures > 0 && ` · ${service.probe.consecutive_failures} failed in a row`}
                  </Field>
                )}
                {service.availability_24h != null && service.group !== "credits" && (
                  <Field label="Availability">{fmtPct(service.availability_24h, 2)} over 24h</Field>
                )}
              </div>

              {service.last_error && (
                <div className="space-y-1.5 rounded-lg border p-3">
                  <p className="text-micro text-muted-foreground">
                    Latest problem · {ago(service.last_error.at, now)}
                    {service.last_error.op ? ` · ${service.last_error.op}` : ""}
                  </p>
                  <p className="break-words font-mono text-xs">{service.last_error.message}</p>
                  {service.last_error.call_session_id && (
                    <div className="flex flex-wrap items-center gap-2 pt-1 text-xs text-muted-foreground">
                      <span>Call</span>
                      <code className="rounded bg-muted px-1.5 py-0.5">{service.last_error.call_session_id}</code>
                      <Button
                        size="xs"
                        variant="ghost"
                        onClick={() => copy(`@call_session_id:${service.last_error!.call_session_id}`)}
                      >
                        <Copy /> Railway log filter
                      </Button>
                    </div>
                  )}
                </div>
              )}

              <DetailExtras detail={service.detail} />

              {incidents.length > 0 && (
                <div className="space-y-1">
                  <p className="text-micro text-muted-foreground">Incidents (24h)</p>
                  {incidents.map((inc) => (
                    <IncidentLine key={inc.id} incident={inc} now={now} compact />
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ── incidents ───────────────────────────────────────────────────────────────

function IncidentLine({ incident, now, compact }: { incident: HealthIncident; now: number; compact?: boolean }) {
  const open = incident.resolved_at == null;
  const length = open ? (now - new Date(incident.opened_at).getTime()) / 1000 : incident.duration_s;
  return (
    <div className="flex items-start gap-3 py-2 text-sm">
      <Dot state={incident.severity} pulse={open && incident.state === "down"} />
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex flex-wrap items-baseline gap-x-2">
          {!compact && <span className="font-medium">{incident.label}</span>}
          <span className="text-xs text-muted-foreground">
            {clock(incident.opened_at)} · {open ? `ongoing for ${duration(length)}` : `lasted ${duration(length)}`}
            {incident.error_count ? ` · ${incident.error_count} failures` : ""}
            {incident.alerts_sent ? ` · ${incident.alerts_sent} email${incident.alerts_sent === 1 ? "" : "s"}` : ""}
          </span>
        </div>
        {incident.first_error && <p className="line-clamp-2 break-words text-xs text-muted-foreground">{incident.first_error}</p>}
      </div>
      <span
        className="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium"
        style={open ? { color: STATE[incident.state].color, backgroundColor: STATE[incident.state].bg } : { color: "var(--status-live)", backgroundColor: "var(--status-live-bg)" }}
      >
        {open ? STATE[incident.state].label : "Resolved"}
      </span>
    </div>
  );
}

// ── page section ────────────────────────────────────────────────────────────

const BANNER = {
  up: { icon: CheckCircle2, title: "All systems operational", state: "up" as HealthState },
  degraded: { icon: AlertTriangle, title: "Some services are degraded", state: "degraded" as HealthState },
  down: { icon: XCircle, title: "Service outage", state: "down" as HealthState },
};

export function SystemHealth() {
  const { snapshot, live, error } = useHealthFeed();
  const now = useNow();
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  const incidentsBy = useMemo(() => {
    const map = new Map<string, HealthIncident[]>();
    for (const inc of snapshot?.incidents ?? []) map.set(inc.service, [...(map.get(inc.service) ?? []), inc]);
    return map;
  }, [snapshot]);

  const services = useMemo(() => snapshot?.groups.flatMap((g) => g.services) ?? [], [snapshot]);
  const openService = services.find((s) => s.key === openKey) ?? null;

  const sendDigest = useCallback(async () => {
    setSending(true);
    try {
      const res = await adminApi.sendHealthDigest();
      if (res.status === "sent") toast.success(`Health digest sent to ${res.recipients?.join(", ")}`);
      else toast.error("Digest wasn't delivered -- check the Resend tile");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't send the digest");
    } finally {
      setSending(false);
    }
  }, []);

  if (!snapshot) {
    return error ? (
      <Card>
        <CardContent className="py-6 text-sm text-destructive">Couldn&apos;t load service health: {error.message}</CardContent>
      </Card>
    ) : (
      <div className="space-y-3">
        <Skeleton className="h-20 w-full rounded-xl" />
        <div className="grid gap-4 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-56 w-full rounded-xl" />
          ))}
        </div>
      </div>
    );
  }

  const banner = BANNER[snapshot.overall];
  const failing = services.filter((s) => s.group !== "credits" && (s.state === "down" || s.state === "degraded"));
  const lowCredits = services.filter((s) => s.group === "credits" && (s.state === "down" || s.state === "degraded"));
  const openIncidents = snapshot.incidents.filter((i) => !i.resolved_at);
  const resolvedIncidents = snapshot.incidents.filter((i) => i.resolved_at);

  return (
    <div className="space-y-4">
      <Card className="overflow-hidden" style={{ borderLeft: `4px solid ${STATE[banner.state].color}` }}>
        <CardContent className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center">
          <banner.icon className="size-7 shrink-0" style={{ color: STATE[banner.state].color }} />
          <div className="min-w-0 flex-1 space-y-0.5">
            <p className="text-lg font-semibold">
              {snapshot.overall === "up" ? banner.title : failing.map((s) => `${s.label} ${stateLabel(s).toLowerCase()}`).join(" · ")}
            </p>
            <p className="text-xs text-muted-foreground">
              {snapshot.environment} ·{" "}
              {snapshot.alerts_enabled
                ? `alerts emailed to ${snapshot.alert_recipients.join(", ")}`
                : "email alerts are off in this environment (production sends them)"}
              {lowCredits.length > 0 && (
                <span style={{ color: "var(--status-pending)" }}> · low credits: {lowCredits.map((s) => s.label).join(", ")}</span>
              )}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              {live ? <Radio className="size-3.5" style={{ color: "var(--status-live)" }} /> : <CircleSlash className="size-3.5" />}
              {live ? "Live" : "Reconnecting"} · {ago(snapshot.generated_at, now)}
            </span>
            <Button size="sm" variant="outline" onClick={sendDigest} disabled={sending}>
              <Mail /> {sending ? "Sending…" : "Send digest now"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
        {snapshot.groups.map((group) => {
          const bad = group.services.filter((s) => s.state === "down" || s.state === "degraded").length;
          return (
            <Section
              key={group.key}
              title={group.label}
              description={bad ? `${bad} need${bad === 1 ? "s" : ""} attention` : `${group.services.filter((s) => s.state === "up").length} healthy`}
            >
              <div className="space-y-2">
                {group.services.map((service) => (
                  <ServiceRow
                    key={service.key}
                    service={service}
                    incidents={incidentsBy.get(service.key) ?? []}
                    now={now}
                    onOpen={() => setOpenKey(service.key)}
                  />
                ))}
              </div>
            </Section>
          );
        })}

        <Section
          title="Incidents"
          description={openIncidents.length ? `${openIncidents.length} ongoing` : "Last 24 hours"}
          className="lg:col-span-2 xl:col-span-3"
        >
          {snapshot.incidents.length === 0 ? (
            <p className="py-2 text-sm text-muted-foreground">No incidents in the last 24 hours.</p>
          ) : (
            <div className="divide-y">
              {[...openIncidents, ...resolvedIncidents].map((inc) => (
                <IncidentLine key={inc.id} incident={inc} now={now} />
              ))}
            </div>
          )}
        </Section>
      </div>

      <ServiceDialog
        service={openService}
        incidents={openService ? incidentsBy.get(openService.key) ?? [] : []}
        onClose={() => setOpenKey(null)}
        now={now}
      />
    </div>
  );
}
