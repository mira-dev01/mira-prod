"use client";

import { Fragment, useState } from "react";
import { useAdminQuery } from "@/components/admin/admin-context";
import {
  BarList,
  CoverageNote,
  Empty,
  ErrorState,
  LoadingGrid,
  PageHeader,
  Section,
  Stat,
  StatGrid,
  fmtAgo,
  fmtNum,
  fmtPct,
  fmtSec,
  humanize,
} from "@/components/admin/admin-ui";
import { Button } from "@/components/ui/button";
import { adminApi, type FlaggedCallDetail, type FlaggedCallRow } from "@/lib/admin-api";

const END_LABELS: Record<string, string> = {
  mira_ended: "Mira closed the call",
  guest_hung_up: "Guest hung up",
  host_handoff: "Handed to host",
  silent_caller: "Silent caller",
  max_duration: "Hit 10-min limit",
};

type CallsTableProps = {
  calls: FlaggedCallRow[];
  openId: string | null;
  detail: FlaggedCallDetail | null;
  detailError: string | null;
  onToggle: (id: string) => void;
};

function CallsTable({ calls, openId, detail, detailError, onToggle }: CallsTableProps) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground">
            <th className="py-2 pr-3 font-medium">When</th>
            <th className="py-2 pr-3 font-medium">Host</th>
            <th className="py-2 pr-3 font-medium">Why</th>
            <th className="py-2 pr-3 font-medium">Ended</th>
            <th className="py-2 pr-3 font-medium">Length</th>
            <th className="py-2" />
          </tr>
        </thead>
        <tbody>
          {calls.map((call) => (
            <Fragment key={call.id}>
              <tr className="border-t align-top">
                <td className="py-2 pr-3 whitespace-nowrap">{fmtAgo(call.created_at)}</td>
                <td className="py-2 pr-3">{call.host ?? "—"}</td>
                <td className="py-2 pr-3">
                  {call.flags.length === 0 ? (
                    <span className="text-muted-foreground">Not flagged</span>
                  ) : (
                    <ul className="space-y-0.5">
                      {call.flags.map((flag) => (
                        <li key={flag.code}>
                          <span className="font-medium">{flag.label}</span>
                          <span className="text-muted-foreground"> · {flag.detail}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
                <td className="py-2 pr-3 whitespace-nowrap">
                  {call.ended_by ? (END_LABELS[call.ended_by] ?? humanize(call.ended_by)) : "—"}
                </td>
                <td className="py-2 pr-3 whitespace-nowrap">{fmtSec(call.duration_s)}</td>
                <td className="py-2 text-right">
                  <Button variant="outline" size="sm" onClick={() => onToggle(call.id)}>
                    {openId === call.id ? "Close" : "Open"}
                  </Button>
                </td>
              </tr>
              {openId === call.id && (
                <tr>
                  <td colSpan={6} className="bg-muted/40 p-3">
                    {detailError && <p className="text-sm text-destructive">{detailError}</p>}
                    {!detail && !detailError && <p className="text-sm text-muted-foreground">Loading…</p>}
                    {detail && (
                      <div className="space-y-3">
                        {detail.recording_url ? (
                          <audio controls src={detail.recording_url} className="w-full" />
                        ) : (
                          <p className="text-sm text-muted-foreground">No recording stored for this call.</p>
                        )}
                        <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-sm">
                          {detail.transcript ?? "No transcript stored."}
                        </pre>
                      </div>
                    )}
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AdminFlaggedCallsPage() {
  const { data, loading, error } = useAdminQuery(adminApi.flaggedCalls);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<FlaggedCallDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  async function toggle(id: string) {
    if (openId === id) {
      setOpenId(null);
      return;
    }
    setOpenId(id);
    setDetail(null);
    setDetailError(null);
    try {
      setDetail(await adminApi.flaggedCallDetail(id));
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : "Could not load the call");
    }
  }

  const table = { openId, detail, detailError, onToggle: toggle };

  return (
    <>
      <PageHeader title="Flagged calls" subtitle="Calls that went wrong, and why — read these first" />
      {error && <ErrorState error={error} />}
      {loading && !data ? (
        <LoadingGrid />
      ) : data ? (
        <>
          <StatGrid className="mb-5 lg:grid-cols-3">
            <Stat label="Flagged calls" value={fmtNum(data.coverage.flagged_calls)} hint={`of ${fmtNum(data.coverage.scored_calls)} scored`} />
            <Stat
              label="Share flagged"
              value={fmtPct(data.coverage.scored_calls ? data.coverage.flagged_calls / data.coverage.scored_calls : null)}
              hint="of calls that have a scorecard"
            />
            <Stat label="Top reason" value={data.reasons[0]?.label ?? "—"} hint={data.reasons[0] ? `${fmtNum(data.reasons[0].count)} calls` : "nothing flagged"} />
          </StatGrid>
          {data.coverage.scored_calls < data.coverage.calls && (
            <CoverageNote>
              {fmtNum(data.coverage.scored_calls)} of {fmtNum(data.coverage.calls)} calls in range have a scorecard; earlier calls
              cannot be flagged here.
            </CoverageNote>
          )}

          {data.weekly_numbers.length > 0 && (
            <div className="mb-5">
              <Section title="Weekly numbers per host" description="The three numbers tracked in the weekly review. Over scored calls only.">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-muted-foreground">
                        <th className="py-2 pr-3 font-medium">Host</th>
                        <th className="py-2 pr-3 font-medium">Scored calls</th>
                        <th className="py-2 pr-3 font-medium">Flagged</th>
                        <th className="py-2 pr-3 font-medium">Ended in a lead, booking or escalation</th>
                        <th className="py-2 font-medium">Guest hung up</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.weekly_numbers.map((row) => (
                        <tr key={row.host} className="border-t">
                          <td className="py-2 pr-3">{row.host}</td>
                          <td className="py-2 pr-3">{fmtNum(row.scored_calls)}</td>
                          <td className="py-2 pr-3">{fmtPct(row.flagged_share)}</td>
                          <td className="py-2 pr-3">{fmtPct(row.outcome_share)}</td>
                          <td className="py-2">{fmtPct(row.guest_hung_up_share)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Section>
            </div>
          )}

          <div className="grid gap-5 lg:grid-cols-3">
            <Section title="Why calls were flagged" description="Most common first">
              <BarList items={data.reasons.map((r) => ({ label: r.label, value: r.count }))} />
            </Section>
            <div className="lg:col-span-2">
              <Section title="Flagged calls" description="Newest first. Open one to read the transcript and play the recording.">
                {data.calls.length === 0 ? <Empty>No flagged calls in this range.</Empty> : <CallsTable calls={data.calls} {...table} />}
              </Section>
            </div>
          </div>

          <div className="mt-5">
            <Section
              title="Ten calls that were not flagged"
              description="A random sample, refreshed on each load. Read these too: they show what the flags miss."
            >
              {data.unflagged_sample.length === 0 ? (
                <Empty>No unflagged scored calls in this range.</Empty>
              ) : (
                <CallsTable calls={data.unflagged_sample} {...table} />
              )}
            </Section>
          </div>
        </>
      ) : null}
    </>
  );
}
