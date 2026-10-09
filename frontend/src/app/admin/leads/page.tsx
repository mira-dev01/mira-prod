"use client";

import { useAdminQuery } from "@/components/admin/admin-context";
import {
  BarList,
  CoverageNote,
  ErrorState,
  LoadingGrid,
  PageHeader,
  Section,
  Stat,
  StatGrid,
  fmtNum,
  fmtPct,
  humanize,
} from "@/components/admin/admin-ui";
import { adminApi } from "@/lib/admin-api";

function fmtMinutes(v: number | null): string {
  if (v == null) return "—";
  return v < 60 ? `${Math.round(v)} min` : `${(v / 60).toFixed(1)} h`;
}

export default function AdminLeadsPage() {
  const { data, loading, error } = useAdminQuery(adminApi.leads);

  return (
    <>
      <PageHeader title="Leads & escalations" subtitle="No genuine guest opportunity should silently disappear" />
      {error && <ErrorState error={error} />}
      {loading && !data ? (
        <LoadingGrid cards={3} />
      ) : data ? (
        <>
          <Section
            title="Lead safety"
            description="Every call that reached pricing or availability must end up with a lead (CLAUDE.md invariant)"
          >
            <StatGrid>
              <Stat label="Leads from calls" value={fmtNum(data.lead_safety.leads_from_calls)} />
              <Stat
                label="Engaged, no lead"
                value={fmtNum(data.lead_safety.engaged_without_lead)}
                hint={`of ${fmtNum(data.lead_safety.calls_with_pricing_engagement)} pricing/calendar calls`}
                tone={data.lead_safety.engaged_without_lead > 0 ? "bad" : "good"}
              />
              <Stat
                label="Caught by safety net"
                value={fmtPct(data.lead_safety.safety_net_share)}
                hint={`${fmtNum(data.lead_safety.leads_via_safety_net)} without update_lead`}
                tone={(data.lead_safety.safety_net_share ?? 0) > 0.5 ? "warn" : undefined}
              />
              <Stat label="No name or phone" value={fmtNum(data.lead_safety.incomplete_leads)} />
            </StatGrid>
            {data.lead_safety.engaged_without_lead_call_ids.length > 0 && (
              <div className="mt-4 rounded-lg bg-[var(--status-pending-bg)] px-3 py-2 text-xs">
                <p className="mb-1 font-medium">Calls to investigate</p>
                <p className="break-all font-mono text-muted-foreground">{data.lead_safety.engaged_without_lead_call_ids.join(", ")}</p>
              </div>
            )}
          </Section>

          <div className="grid gap-5 lg:grid-cols-2">
            <Section title="Busy call recovery" description="Callers rejected because the host was already on a call">
              <StatGrid className="lg:grid-cols-3">
                <Stat label="Rejected as busy" value={fmtNum(data.busy_recovery.busy_rejections)} />
                <Stat label="Recovered as leads" value={fmtPct(data.busy_recovery.recovery_rate)} hint={`${fmtNum(data.busy_recovery.recovery_leads)} leads`} />
                <Stat label="Contacted / booked" value={fmtNum(data.busy_recovery.progressed)} />
              </StatGrid>
              <div className="mt-5">
                <p className="text-micro mb-2 text-muted-foreground">Availability follow-up status</p>
                <BarList colorVar="--status-progress" items={data.busy_recovery.availability_status.map((s) => ({ label: humanize(s.status), value: s.count }))} />
              </div>
            </Section>

            <Section title="Escalations" description="Guests Mira handed to the host">
              <StatGrid className="lg:grid-cols-3">
                <Stat label="Escalations" value={fmtNum(data.escalations.total)} hint={`${fmtNum(data.escalations.responded)} responded`} />
                <Stat label="Host response p50" value={fmtMinutes(data.escalations.response_minutes.p50)} hint={`p90 ${fmtMinutes(data.escalations.response_minutes.p90)}`} />
                <Stat
                  label="Waiting > 1h"
                  value={fmtNum(data.escalations.unresponded_over_1h)}
                  tone={data.escalations.unresponded_over_1h > 0 ? "bad" : "good"}
                />
              </StatGrid>
              <div className="mt-5 grid gap-5 sm:grid-cols-2">
                <div>
                  <p className="text-micro mb-2 text-muted-foreground">Urgency</p>
                  <BarList colorVar="--destructive" items={data.escalations.by_urgency.map((u) => ({ label: humanize(u.urgency), value: u.count }))} />
                </div>
                <div>
                  <p className="text-micro mb-2 text-muted-foreground">Status</p>
                  <BarList colorVar="--priority-low" items={data.escalations.by_status.map((s) => ({ label: humanize(s.status), value: s.count }))} />
                </div>
              </div>
            </Section>
          </div>
          <CoverageNote>
            &quot;Engaged, no lead&quot; and the safety-net split need per-call telemetry ({fmtNum(data.coverage.calls_with_call_metrics)} of{" "}
            {fmtNum(data.coverage.calls)} calls). Busy recovery and escalations use existing records and cover every call.
          </CoverageNote>
        </>
      ) : null}
    </>
  );
}
