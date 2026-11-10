"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { useAdminQuery } from "@/components/admin/admin-context";
import {
  Empty,
  ErrorState,
  LoadingGrid,
  PageHeader,
  Section,
  Stat,
  StatGrid,
  fmtAgo,
  fmtMoney,
  fmtNum,
  fmtSec,
  humanize,
} from "@/components/admin/admin-ui";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { adminApi, type HostDetailData } from "@/lib/admin-api";

const COLLECT_FIELDS = ["dates", "guests", "purpose", "location", "budget", "email"] as const;

function collectSummary(agent: HostDetailData["agent"]): string {
  const asked = COLLECT_FIELDS.filter((f) => agent.collect[f] !== "off").map(
    (f) => `${f}${agent.collect[f] === "required" ? " (must have)" : ""}`
  );
  return ["name", "phone", ...asked].join(", ");
}

function paymentSummary(agent: HostDetailData["agent"]): string {
  const p = agent.payment;
  const how = p.kind === "split" ? `${p.split_percent}% up front` : "Paid in full";
  const confirm = agent.booking.confirm_by === "payment" ? "booking confirmed by payment" : "host confirms each booking";
  return `${how}, dates held ${p.hold_minutes} min · ${confirm}`;
}

function busySummary(agent: HostDetailData["agent"]): string {
  const b = agent.busy_calls;
  return b.mode === "host_follows_up"
    ? "Host follows up themselves"
    : `Guest asked to call back; host ticket after ${b.callback_wait_minutes} min`;
}

function when(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

export default function AdminHostDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data, loading, error } = useAdminQuery((f) => adminApi.hostDetail(id, f));

  return (
    <>
      <Link href="/admin/hosts" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" /> All hosts
      </Link>
      {error && <ErrorState error={error} />}
      {loading && !data ? (
        <LoadingGrid cards={4} />
      ) : data ? (
        <>
          <PageHeader title={data.summary.name} subtitle={`${data.summary.email} · joined ${when(data.summary.joined_at)}`} />

          {data.summary.attention.length > 0 && (
            <Section title="Needs attention">
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {data.summary.attention.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            </Section>
          )}

          <Section title="This range">
            <StatGrid>
              <Stat label="Calls" value={fmtNum(data.summary.calls)} hint={`${fmtNum(data.summary.missed_calls)} missed`} />
              <Stat label="Escalations" value={fmtNum(data.summary.escalations)} hint={`${fmtNum(data.summary.escalations_unread)} unopened`} />
              <Stat label="Open tickets" value={fmtNum(data.summary.open_tickets)} hint={`${fmtNum(data.summary.takeover)} to take over`} />
              <Stat
                label="Questions waiting"
                value={fmtNum(data.summary.questions_pending)}
                tone={data.summary.questions_overdue > 0 ? "bad" : undefined}
                hint={data.summary.questions_overdue > 0 ? `${fmtNum(data.summary.questions_overdue)} over a day` : undefined}
              />
              <Stat label="Cost" value={fmtMoney(data.summary.cost_inr)} />
              <Stat label="Last call" value={data.summary.last_call_at ? `${fmtAgo(data.summary.last_call_at)} ago` : "—"} />
            </StatGrid>
          </Section>

          <Section title="Settings" description="What this host has chosen on the Agent page, and how they can be reached.">
            <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[10rem_1fr]">
              <dt className="text-muted-foreground">Mira asks for</dt>
              <dd>{collectSummary(data.agent)}</dd>
              <dt className="text-muted-foreground">Hot lead when</dt>
              <dd>{data.agent.hot_lead.hot_on.length ? data.agent.hot_lead.hot_on.map(humanize).join(", ") : "Only when the guest asks to book"}</dd>
              <dt className="text-muted-foreground">Payment</dt>
              <dd>{paymentSummary(data.agent)}</dd>
              <dt className="text-muted-foreground">Busy calls</dt>
              <dd>{busySummary(data.agent)}</dd>
              <dt className="text-muted-foreground">Phone</dt>
              <dd>{data.profile.phone ?? "—"}</dd>
              <dt className="text-muted-foreground">Portfolio line</dt>
              <dd>{data.profile.lead_number ?? "—"}</dd>
              <dt className="text-muted-foreground">Alerts go to</dt>
              <dd>{data.profile.notification_email ?? data.summary.email}</dd>
              <dt className="text-muted-foreground">Setup</dt>
              <dd>{data.summary.onboarding ? humanize(data.summary.onboarding) : "—"}</dd>
            </dl>
            {data.capabilities.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-1.5">
                {data.capabilities.map((c) => (
                  <span
                    key={c.id}
                    className="rounded-sm border border-border px-2 py-0.5 text-xs"
                    style={c.enabled ? undefined : { opacity: 0.5, textDecoration: "line-through" }}
                    title={`${c.enabled ? "On" : "Off"} · ${humanize(c.state)}`}
                  >
                    {c.name}
                  </span>
                ))}
              </div>
            )}
          </Section>

          <Section title="Properties">
            {data.properties.length === 0 ? (
              <Empty>No properties yet</Empty>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Property</TableHead>
                      <TableHead>Number</TableHead>
                      <TableHead>Payment</TableHead>
                      <TableHead className="text-right">Calls</TableHead>
                      <TableHead className="text-right">Questions waiting</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.properties.map((p) => (
                      <TableRow key={p.id}>
                        <TableCell>
                          <p className="font-medium">{p.name}</p>
                          {p.city && <p className="text-xs text-muted-foreground">{p.city}</p>}
                        </TableCell>
                        <TableCell className={p.number_connected ? undefined : "text-destructive"}>
                          {p.number_connected ? "Connected" : "None"}
                        </TableCell>
                        <TableCell className="text-muted-foreground">{p.payment_override ? "Own rules" : "Portfolio rules"}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtNum(p.calls)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtNum(p.questions_pending)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </Section>

          <Section title="Open tickets" description="Follow-ups Mira promised guests that this host has not yet done.">
            {data.tickets.length === 0 ? (
              <Empty>Nothing open</Empty>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>What</TableHead>
                      <TableHead>Guest</TableHead>
                      <TableHead>Property</TableHead>
                      <TableHead>Urgency</TableHead>
                      <TableHead className="text-right">Waiting</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.tickets.map((t) => (
                      <TableRow key={t.id}>
                        <TableCell>
                          <p>{t.summary}</p>
                          <p className="text-xs text-muted-foreground">{humanize(t.kind)}</p>
                        </TableCell>
                        <TableCell className="text-muted-foreground">{t.guest_name ?? t.guest_phone ?? "—"}</TableCell>
                        <TableCell className="text-muted-foreground">{t.property_name ?? "—"}</TableCell>
                        <TableCell>{humanize(t.urgency)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtAgo(t.created_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </Section>

          <Section title="Questions waiting for an answer" description="Oldest first. Red means unanswered for over a day.">
            {data.questions.length === 0 ? (
              <Empty>No unanswered questions</Empty>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Question</TableHead>
                      <TableHead>Property</TableHead>
                      <TableHead className="text-right">Asked</TableHead>
                      <TableHead className="text-right">Waiting</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.questions.map((q, i) => (
                      <TableRow key={`${q.question}-${i}`}>
                        <TableCell>{q.question}</TableCell>
                        <TableCell className="text-muted-foreground">{q.property_name ?? "All properties"}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtNum(q.asked)}×</TableCell>
                        <TableCell className={`text-right tabular-nums ${q.overdue ? "text-destructive" : ""}`}>
                          {fmtAgo(q.first_asked_at)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </Section>

          <Section title="Recent escalations">
            {data.escalations.length === 0 ? (
              <Empty>None</Empty>
            ) : (
              <ul className="divide-y text-sm">
                {data.escalations.map((e, i) => (
                  <li key={i} className="flex items-start justify-between gap-4 py-2">
                    <span>
                      {e.message}
                      <span className="ml-2 text-xs text-muted-foreground">
                        {humanize(e.urgency)} · {e.status === "new" ? "unopened" : "opened"}
                      </span>
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">{fmtAgo(e.created_at)} ago</span>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title="Recent calls" description="Latest 15 in the selected range.">
            {data.recent_calls.length === 0 ? (
              <Empty>No calls in this range</Empty>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>When</TableHead>
                      <TableHead>Caller</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="text-right">Length</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.recent_calls.map((c) => (
                      <TableRow key={c.id}>
                        <TableCell>{when(c.created_at)}</TableCell>
                        <TableCell className="text-muted-foreground">{c.caller_number ?? "—"}</TableCell>
                        <TableCell>{humanize(c.call_type.toLowerCase())}</TableCell>
                        <TableCell className="text-muted-foreground">{humanize(c.status)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtSec(c.duration_s)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </Section>
        </>
      ) : null}
    </>
  );
}
