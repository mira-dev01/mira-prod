"use client";

import { useAdminQuery } from "@/components/admin/admin-context";
import {
  CoverageNote,
  ErrorState,
  Histogram,
  LoadingGrid,
  PageHeader,
  Section,
  Stat,
  StatGrid,
  fmtNum,
  fmtPct,
  fmtSec,
} from "@/components/admin/admin-ui";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { adminApi } from "@/lib/admin-api";

export default function AdminPerformancePage() {
  const { data, loading, error } = useAdminQuery(adminApi.performance);

  return (
    <>
      <PageHeader title="Speed & reliability" subtitle="How fast Mira answers, and what fails underneath" />
      {error && <ErrorState error={error} />}
      {loading && !data ? (
        <LoadingGrid />
      ) : data ? (
        <>
          <div className="grid gap-5 lg:grid-cols-2">
            <Section
              title="Response latency"
              description={`From the guest's turn closing to Mira's first audio. Guests also wait the ${data.latency.turn_detection_wait_s}s turn-detection pause before this.`}
            >
              <StatGrid className="lg:grid-cols-3">
                <Stat
                  label="p50"
                  value={fmtSec(data.latency.response_s.p50)}
                  tone={(data.latency.response_s.p50 ?? 0) > 2 ? "warn" : "good"}
                />
                <Stat label="p90" value={fmtSec(data.latency.response_s.p90)} />
                <Stat label="p95" value={fmtSec(data.latency.response_s.p95)} hint={`${fmtNum(data.latency.response_s.samples)} turns`} />
              </StatGrid>
              <div className="mt-5">
                <Histogram buckets={data.latency.response_histogram} label={(b) => `${b.from}s`} highlight={(b) => b.from >= 3} />
              </div>
            </Section>

            <Section title="Time to first byte" description="Per provider, per request">
              <StatGrid className="lg:grid-cols-2">
                <Stat label="LLM p50" value={fmtSec(data.latency.llm_ttfb_s.p50)} hint={`p95 ${fmtSec(data.latency.llm_ttfb_s.p95)}`} />
                <Stat label="TTS p50" value={fmtSec(data.latency.tts_ttfb_s.p50)} hint={`p95 ${fmtSec(data.latency.tts_ttfb_s.p95)}`} />
                <Stat
                  label="Fallback model share"
                  value={fmtPct(data.llm.fallback_share, 1)}
                  hint={`primary: ${data.llm.primary_model}`}
                  tone={(data.llm.fallback_share ?? 0) > 0.1 ? "warn" : undefined}
                />
                <Stat
                  label="Calls with errors"
                  value={fmtPct(data.llm.calls_with_errors_share, 1)}
                  hint={`${fmtNum(data.llm.pipeline_errors)} errors · ${fmtNum(data.llm.system_failures)} system failures`}
                  tone={(data.llm.calls_with_errors_share ?? 0) > 0.05 ? "bad" : undefined}
                />
              </StatGrid>
            </Section>
          </div>

          <Section title="Tools" description="Every tool Mira called — volume, failures and duration">
            {data.tools.length === 0 ? (
              <p className="py-4 text-center text-sm text-muted-foreground">No tool calls recorded in this range</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Tool</TableHead>
                    <TableHead className="text-right">Calls</TableHead>
                    <TableHead className="text-right">Errors</TableHead>
                    <TableHead className="text-right">p50</TableHead>
                    <TableHead className="text-right">p95</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.tools.map((t) => (
                    <TableRow key={t.name}>
                      <TableCell className="font-mono text-xs">{t.name}</TableCell>
                      <TableCell className="text-right tabular-nums">{fmtNum(t.calls)}</TableCell>
                      <TableCell
                        className="text-right tabular-nums"
                        style={(t.error_rate ?? 0) > 0.05 ? { color: "var(--destructive)" } : undefined}
                      >
                        {fmtNum(t.errors)} <span className="text-xs text-muted-foreground">{fmtPct(t.error_rate)}</span>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{fmtSec(t.p50_s)}</TableCell>
                      <TableCell className="text-right tabular-nums">{fmtSec(t.p95_s)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Section>

          <Section title="LLM models" description="Which model actually served each completion during calls">
            {data.llm.models.length === 0 ? (
              <p className="py-4 text-center text-sm text-muted-foreground">No completions recorded in this range</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Provider</TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead className="text-right">Completions</TableHead>
                    <TableHead className="text-right">Share</TableHead>
                    <TableHead className="text-right">Prompt tokens</TableHead>
                    <TableHead className="text-right">Completion tokens</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.llm.models.map((m) => (
                    <TableRow key={`${m.provider}-${m.model}`}>
                      <TableCell className="capitalize">{m.provider}</TableCell>
                      <TableCell className="font-mono text-xs">{m.model ?? "—"}</TableCell>
                      <TableCell className="text-right tabular-nums">{fmtNum(m.completions)}</TableCell>
                      <TableCell className="text-right tabular-nums">{fmtPct(m.share)}</TableCell>
                      <TableCell className="text-right tabular-nums">{fmtNum(m.prompt_tokens)}</TableCell>
                      <TableCell className="text-right tabular-nums">{fmtNum(m.completion_tokens)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Section>
          <CoverageNote>
            From per-call telemetry: {fmtNum(data.coverage.calls_with_call_metrics)} of {fmtNum(data.coverage.calls)} calls in this range.
          </CoverageNote>
        </>
      ) : null}
    </>
  );
}
