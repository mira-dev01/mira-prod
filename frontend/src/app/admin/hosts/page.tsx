"use client";

import Link from "next/link";
import { useAdminQuery } from "@/components/admin/admin-context";
import { Empty, ErrorState, LoadingGrid, PageHeader, Section, fmtAgo, fmtMoney, fmtNum, fmtPct, fmtSec } from "@/components/admin/admin-ui";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { adminApi } from "@/lib/admin-api";

function rel(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

/** A count that turns red when something in it has waited too long. */
function Waiting({ n, one, many, overdue }: { n: number; one: string; many: string; overdue?: boolean }) {
  if (!n) return null;
  return (
    <p className={overdue ? "text-destructive" : "text-muted-foreground"}>
      {fmtNum(n)} {n === 1 ? one : many}
    </p>
  );
}

export default function AdminHostsPage() {
  const { data, loading, error } = useAdminQuery(adminApi.hosts);
  const roster = useAdminQuery(adminApi.hostRoster);

  return (
    <>
      <PageHeader title="Hosts & properties" subtitle="Who needs attention, and where the volume, the problems and the cost are" />
      {roster.error && <ErrorState error={roster.error} />}
      {roster.loading && !roster.data ? (
        <LoadingGrid cards={2} />
      ) : roster.data ? (
        <>
          {roster.data.takeover.length > 0 && (
            <Section
              title="Needs takeover"
              description="Calls Mira could not take, still waiting for someone to call the guest back. Longest-waiting first."
            >
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Host</TableHead>
                      <TableHead>What to do</TableHead>
                      <TableHead>Property</TableHead>
                      <TableHead className="text-right">Waiting</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {roster.data.takeover.map((t) => (
                      <TableRow key={t.id}>
                        <TableCell>
                          <Link href={`/admin/hosts/${t.user_id}`} className="font-medium hover:underline">
                            {t.host}
                          </Link>
                        </TableCell>
                        <TableCell>{t.summary}</TableCell>
                        <TableCell className="text-muted-foreground">{t.property_name ?? "—"}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtAgo(t.created_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </Section>
          )}

          <Section
            title="Hosts at a glance"
            description="Everyone who has signed up, including hosts with no calls. Those needing attention come first."
          >
            {roster.data.hosts.length === 0 ? (
              <Empty>No hosts yet</Empty>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Host</TableHead>
                      <TableHead>Setup</TableHead>
                      <TableHead>Waiting on the host</TableHead>
                      <TableHead className="text-right">Calls</TableHead>
                      <TableHead className="text-right">Cost</TableHead>
                      <TableHead className="text-right">Last call</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {roster.data.hosts.map((h) => (
                      <TableRow key={h.user_id}>
                        <TableCell className="align-top">
                          <Link href={`/admin/hosts/${h.user_id}`} className="font-medium hover:underline">
                            {h.name}
                          </Link>
                          <p className="text-xs text-muted-foreground">{h.email}</p>
                          {h.attention.length > 0 && (
                            <p className="mt-1 text-xs" style={{ color: "var(--status-pending)" }}>
                              {h.attention.join(" · ")}
                            </p>
                          )}
                        </TableCell>
                        <TableCell className="align-top text-sm">
                          <p>{fmtNum(h.properties)} {h.properties === 1 ? "property" : "properties"}</p>
                          <p className={h.number_connected ? "text-muted-foreground" : "text-destructive"}>
                            {h.number_connected ? "Number connected" : "No number"}
                          </p>
                        </TableCell>
                        <TableCell className="align-top text-sm">
                          <Waiting n={h.takeover} one="call to take over" many="calls to take over" overdue />
                          <Waiting n={h.open_tickets - h.takeover} one="other ticket" many="other tickets" />
                          <Waiting n={h.questions_pending} one="question" many="questions" overdue={h.questions_overdue > 0} />
                          <Waiting n={h.escalations_unread} one="escalation unopened" many="escalations unopened" overdue={h.escalations_overdue} />
                          {!h.takeover && !h.open_tickets && !h.questions_pending && !h.escalations_unread && (
                            <p className="text-muted-foreground">Nothing</p>
                          )}
                        </TableCell>
                        <TableCell className="text-right align-top tabular-nums">
                          {fmtNum(h.calls)}
                          {h.missed_calls > 0 && <p className="text-xs text-muted-foreground">{fmtNum(h.missed_calls)} missed</p>}
                        </TableCell>
                        <TableCell className="text-right align-top tabular-nums">{fmtMoney(h.cost_inr)}</TableCell>
                        <TableCell className="text-right align-top text-muted-foreground">{rel(h.last_call_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </Section>
        </>
      ) : null}

      {error && <ErrorState error={error} />}
      {loading && !data ? (
        <LoadingGrid cards={2} />
      ) : data ? (
        <>
          <Section title="Call quality by host" description="Hosts with calls in this range, by volume. Cost covers metered usage plus telephony minutes.">
            {data.hosts.length === 0 ? (
              <p className="py-4 text-center text-sm text-muted-foreground">No calls in this range</p>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Host</TableHead>
                      <TableHead className="text-right">Calls</TableHead>
                      <TableHead className="text-right">Answered</TableHead>
                      <TableHead className="text-right">Busy</TableHead>
                      <TableHead className="text-right">Leads</TableHead>
                      <TableHead className="text-right">Escalations</TableHead>
                      <TableHead className="text-right">Avg length</TableHead>
                      <TableHead className="text-right">Guards / call</TableHead>
                      <TableHead className="text-right">Response p50</TableHead>
                      <TableHead className="text-right">Cost</TableHead>
                      <TableHead className="text-right">Per call</TableHead>
                      <TableHead className="text-right">Last call</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.hosts.map((h) => (
                      <TableRow key={h.user_id ?? h.name}>
                        <TableCell>
                          <p className="font-medium">{h.name}</p>
                          {h.email && <p className="text-xs text-muted-foreground">{h.email}</p>}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{fmtNum(h.calls)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtNum(h.answered)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtNum(h.busy_rejected)}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          {fmtNum(h.leads)} <span className="text-xs text-muted-foreground">{fmtPct(h.answered ? h.leads / h.answered : null)}</span>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{fmtNum(h.escalations)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtSec(h.avg_duration_s)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtNum(h.guard_firings_per_call, 2)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtSec(h.response_p50_s)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtMoney(h.cost_inr)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtMoney(h.cost_per_answered_inr)}</TableCell>
                        <TableCell className="text-right text-muted-foreground">{rel(h.last_call_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </Section>

          <Section title="Properties" description="Top 50 by call volume">
            {data.properties.length === 0 ? (
              <p className="py-4 text-center text-sm text-muted-foreground">No property-scoped calls in this range</p>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Property</TableHead>
                      <TableHead className="text-right">Calls</TableHead>
                      <TableHead className="text-right">Answered</TableHead>
                      <TableHead className="text-right">Leads</TableHead>
                      <TableHead className="text-right">Escalations</TableHead>
                      <TableHead className="text-right">Avg length</TableHead>
                      <TableHead className="text-right">Guards / call</TableHead>
                      <TableHead className="text-right">Response p50</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.properties.map((p) => (
                      <TableRow key={p.property_id}>
                        <TableCell>
                          <p className="font-medium">{p.name}</p>
                          {p.city && <p className="text-xs text-muted-foreground">{p.city}</p>}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{fmtNum(p.calls)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtNum(p.answered)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtNum(p.leads)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtNum(p.escalations)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtSec(p.avg_duration_s)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtNum(p.guard_firings_per_call, 2)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtSec(p.response_p50_s)}</TableCell>
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
