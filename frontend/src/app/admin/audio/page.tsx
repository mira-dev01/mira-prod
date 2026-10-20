"use client";

import { useAdminQuery } from "@/components/admin/admin-context";
import {
  BarList,
  CoverageNote,
  DecisionList,
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
  humanize,
} from "@/components/admin/admin-ui";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { adminApi } from "@/lib/admin-api";

export default function AdminAudioPage() {
  const { data, loading, error } = useAdminQuery(adminApi.audio);

  return (
    <>
      <PageHeader
        title="Voice & audio"
        subtitle="Phase 1A shadow week — what each candidate fix would have done, without acting on it"
      />
      {error && <ErrorState error={error} />}
      {loading && !data ? (
        <LoadingGrid cards={6} />
      ) : data ? (
        <>
          <CoverageNote>
            {fmtNum(data.coverage.calls_with_audio_telemetry)} of {fmtNum(data.coverage.answered_calls)} answered calls carry
            audio telemetry. Verdicts need at least {data.coverage.min_calls_for_decision}. Segment-level charts sample the{" "}
            {fmtNum(data.coverage.segment_sample_calls)} most recent calls ({fmtNum(data.coverage.segment_sample_size)} segments).
          </CoverageNote>

          <Section title="Phase 1B decisions" description="Each candidate fix, the metric that decides it, and the current verdict">
            <DecisionList decisions={data.decisions} />
          </Section>

          <div className="grid gap-5 lg:grid-cols-2">
            <Section title="Barge-ins" description="Mira was cut off while speaking">
              <StatGrid className="lg:grid-cols-3">
                <Stat label="Total" value={fmtNum(data.interruptions.barge_ins_total)} hint={`${fmtNum(data.interruptions.barge_ins_per_call, 1)} per call`} />
                <Stat
                  label="Phantom"
                  value={fmtPct(data.interruptions.phantom_rate)}
                  hint={`${fmtNum(data.interruptions.phantom_barge_ins)} with no real words`}
                  tone={(data.interruptions.phantom_rate ?? 0) > 0.2 ? "bad" : undefined}
                />
                <Stat
                  label="Sarvam VAD active"
                  value={fmtPct(data.interruptions.sarvam_vad_share)}
                  hint={`${fmtNum(data.interruptions.calls_with_sarvam_vad_events)} calls`}
                />
              </StatGrid>
              <div className="mt-5 grid gap-5 sm:grid-cols-2">
                <div>
                  <p className="text-micro mb-2 text-muted-foreground">Who interrupted</p>
                  <BarList
                    items={[
                      { label: "Sarvam server VAD", value: data.interruptions.by_source.sarvam_stt },
                      { label: "Local turn detector", value: data.interruptions.by_source.downstream_turn_controller },
                    ]}
                  />
                  <p className="mt-2 text-xs text-muted-foreground">
                    {fmtNum(data.interruptions.dual_source)} fired from both · {fmtNum(data.interruptions.transcript_triggered)} started by a transcript
                  </p>
                </div>
                <div>
                  <p className="text-micro mb-2 text-muted-foreground">What followed</p>
                  <BarList
                    colorVar="--status-progress"
                    items={Object.entries(data.interruptions.outcomes).map(([k, v]) => ({ label: humanize(k), value: v }))}
                  />
                </div>
              </div>
            </Section>

            <Section title="Shadow rules" description="How often each rule would have acted">
              <StatGrid className="lg:grid-cols-3">
                <Stat label="Echo drops" value={fmtNum(data.shadow.echo_drop)} hint={`${fmtNum(data.shadow.echo_per_100_segments, 2)} / 100 segments`} />
                <Stat label="1-word drops" value={fmtNum(data.shadow.short_drop)} hint="while Mira spoke" />
                <Stat
                  label="Quiet speaker"
                  value={fmtNum(data.shadow.level_drop + data.shadow.level_clarify)}
                  hint={`${fmtNum(data.shadow.level_drop)} drop · ${fmtNum(data.shadow.level_clarify)} ask again`}
                />
              </StatGrid>
              <div className="mt-5">
                <p className="text-micro mb-2 text-muted-foreground">1-word drops by kind</p>
                <BarList
                  colorVar="--status-pending"
                  items={Object.entries(data.shadow.short_drop_by_class).map(([k, v]) => ({ label: humanize(k), value: v }))}
                />
                <p className="mt-2 text-xs text-muted-foreground">
                  A high &quot;Number&quot; count means guests answer questions early — add numbers to the allow-list before enabling.
                </p>
              </div>
            </Section>

            <Section title="Low-confidence guard" description='The "could you say that again?" substitution'>
              <StatGrid className="lg:grid-cols-3">
                <Stat label="Triggers" value={fmtNum(data.guard.triggers)} hint={`${fmtPct(data.guard.trigger_rate, 1)} of segments`} />
                <Stat
                  label="On short replies"
                  value={fmtPct(data.guard.short_share_of_triggers)}
                  hint={`${fmtNum(data.guard.short_triggers)} on ≤ 2 words`}
                  tone={(data.guard.short_share_of_triggers ?? 0) >= 0.5 ? "warn" : undefined}
                />
                <Stat label="STT latency p50" value={fmtSec(data.stt_latency_s.p50)} hint={`p95 ${fmtSec(data.stt_latency_s.p95)}`} />
              </StatGrid>
              <div className="mt-5">
                <p className="text-micro mb-2 text-muted-foreground">Sarvam language_probability (guard fires below 0.4)</p>
                <Histogram
                  buckets={data.levels.lang_prob_histogram}
                  label={(b) => b.from.toFixed(1)}
                  highlight={(b) => b.to <= 0.4}
                />
              </div>
            </Section>

            <Section title="Line quality" description="Background noise and how loud each segment was versus the caller">
              <StatGrid className="lg:grid-cols-3">
                <Stat label="SNR p50" value={data.levels.snr_db.p50 == null ? "—" : `${data.levels.snr_db.p50} dB`} hint={`p10 ${data.levels.snr_db.p10 ?? "—"} dB`} />
                <Stat label="Noise floor p50" value={data.levels.noise_floor_dbfs_p50 == null ? "—" : `${data.levels.noise_floor_dbfs_p50} dBFS`} />
                <Stat label="Observer cost" value={data.overhead_ms.p50 == null ? "—" : `${data.overhead_ms.p50} ms`} hint="per call, p50" />
              </StatGrid>
              <div className="mt-5">
                <p className="text-micro mb-2 text-muted-foreground">Segment loudness vs caller baseline (dB) — quiet rule at −12</p>
                <Histogram
                  buckets={data.levels.relative_db_histogram}
                  label={(b) => `${b.from}`}
                  colorVar="--status-live"
                  highlight={(b) => b.to <= -12}
                />
              </div>
            </Section>
          </div>

          <Section
            title="Most frequent short replies"
            description="Segments of 1–2 words — the input for the Phase 1B stop-word allow-list"
          >
            {data.top_short_texts.length === 0 ? (
              <p className="py-4 text-center text-sm text-muted-foreground">No short segments yet</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Text</TableHead>
                    <TableHead>Kind</TableHead>
                    <TableHead className="text-right">Count</TableHead>
                    <TableHead className="text-right">While Mira spoke</TableHead>
                    <TableHead className="text-right">Guard fired</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.top_short_texts.map((row) => (
                    <TableRow key={row.text}>
                      <TableCell className="font-medium">{row.text}</TableCell>
                      <TableCell className="text-muted-foreground">{row.token_class ? humanize(row.token_class) : "—"}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.count}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.while_bot_speaking}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.guard_triggered}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Section>
        </>
      ) : null}
    </>
  );
}
