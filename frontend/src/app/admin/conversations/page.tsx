"use client";

import { useAdminQuery } from "@/components/admin/admin-context";
import {
  BarList,
  CoverageNote,
  ErrorState,
  Funnel,
  LoadingGrid,
  PageHeader,
  Section,
  Stat,
  StatGrid,
  fmtNum,
  fmtPct,
  fmtSec,
  humanize,
} from "@/components/admin/admin-ui";
import { adminApi } from "@/lib/admin-api";

const END_LABELS: Record<string, string> = {
  mira_ended: "Mira closed the call",
  guest_hung_up: "Guest hung up",
  host_handoff: "Handed to host",
  silent_caller: "Silent caller",
  max_duration: "Hit 10-min limit",
  busy_rejected: "Busy — rejected",
  system_failure: "System failure",
  unknown: "Unknown (pre-telemetry)",
};

const LANGUAGE_LABELS: Record<string, string> = { "hi-IN": "Hindi", "en-IN": "English" };

export default function AdminConversationsPage() {
  const { data, loading, error } = useAdminQuery(adminApi.conversations);

  return (
    <>
      <PageHeader title="Conversations" subtitle="Funnel, outcomes, understanding and language" />
      {error && <ErrorState error={error} />}
      {loading && !data ? (
        <LoadingGrid />
      ) : data ? (
        <>
          <div className="grid gap-5 lg:grid-cols-2">
            <Section title="Funnel" description="Each step's % is relative to the step above. * = telemetry calls only.">
              <Funnel steps={data.funnel} />
            </Section>

            <Section title="How calls end" description="Who or what ended each call">
              <BarList items={data.outcomes.by_end.map((e) => ({ label: END_LABELS[e.end] ?? humanize(e.end), value: e.count }))} />
            </Section>

            <Section title="Outcomes" description="Duration and what happened after the call">
              <StatGrid className="lg:grid-cols-3">
                <Stat label="Avg duration" value={fmtSec(data.outcomes.duration_s.avg)} hint={`p90 ${fmtSec(data.outcomes.duration_s.p90)}`} />
                <Stat
                  label="Under 20s"
                  value={fmtPct(data.outcomes.short_call_share)}
                  hint={`${fmtNum(data.outcomes.short_calls_under_20s)} answered calls`}
                  tone={(data.outcomes.short_call_share ?? 0) > 0.25 ? "warn" : undefined}
                />
                <Stat label="Escalated" value={fmtNum(data.outcomes.escalated)} hint={`${fmtNum(data.outcomes.transferred_to_host)} transferred`} />
              </StatGrid>
              <div className="mt-5">
                <p className="text-micro mb-2 text-muted-foreground">Call type</p>
                <BarList colorVar="--status-progress" items={data.outcomes.by_call_type.map((c) => ({ label: humanize(c.call_type.toLowerCase()), value: c.count }))} />
              </div>
            </Section>

            <Section title="Understanding" description="Guard firings and clarification prompts">
              <StatGrid className="lg:grid-cols-3">
                <Stat label="Say-again prompts" value={fmtNum(data.understanding.clarifications_total)} hint={`${fmtNum(data.understanding.clarifications_per_call, 2)} per call`} />
                <Stat
                  label="Calls with one"
                  value={fmtPct(data.understanding.calls_with_clarification_share)}
                  tone={(data.understanding.calls_with_clarification_share ?? 0) > 0.3 ? "warn" : undefined}
                />
              </StatGrid>
              <div className="mt-5">
                <p className="text-micro mb-2 text-muted-foreground">Guard firings per 100 answered calls</p>
                <BarList
                  colorVar="--status-pending"
                  items={data.understanding.guard_firings.map((g) => ({ label: humanize(g.rule), value: g.per_100_calls ?? 0, hint: `(${g.count})` }))}
                  format={(v) => fmtNum(v, 1)}
                  emptyLabel="No guard fired in this range"
                />
              </div>
            </Section>

            <Section title="Language" description="What guests speak (multi-word segments only)" className="lg:col-span-2">
              <div className="grid gap-6 md:grid-cols-3">
                <div>
                  <p className="text-micro mb-2 text-muted-foreground">Segments</p>
                  <BarList items={data.language.segments_by_language.map((l) => ({ label: LANGUAGE_LABELS[l.language] ?? l.language, value: l.segments }))} />
                </div>
                <div>
                  <p className="text-micro mb-2 text-muted-foreground">Calls by main language</p>
                  <BarList colorVar="--status-purple" items={data.language.calls_by_dominant_language.map((l) => ({ label: LANGUAGE_LABELS[l.language] ?? l.language, value: l.calls }))} />
                </div>
                <StatGrid className="grid-cols-2 sm:grid-cols-2 lg:grid-cols-2">
                  <Stat label="Switches / call" value={fmtNum(data.language.avg_switches_per_call, 2)} />
                  <Stat label="Calls that switch" value={fmtPct(data.language.calls_with_switch_share)} />
                </StatGrid>
              </div>
            </Section>
          </div>
          <CoverageNote>
            Funnel steps marked * and the end-of-call breakdown come from per-call telemetry ({fmtNum(data.coverage.calls_with_call_metrics)} of{" "}
            {fmtNum(data.coverage.calls)} calls); language and say-again prompts from audio telemetry ({fmtNum(data.coverage.calls_with_audio_telemetry)}).
          </CoverageNote>
        </>
      ) : null}
    </>
  );
}
