"use client";

import { useAdminQuery } from "@/components/admin/admin-context";
import { ErrorState, LoadingGrid, PageHeader, Section, fmtMoney, fmtNum, fmtPct, fmtSec } from "@/components/admin/admin-ui";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { adminApi } from "@/lib/admin-api";

function rel(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

export default function AdminHostsPage() {
  const { data, loading, error } = useAdminQuery(adminApi.hosts);

  return (
    <>
      <PageHeader title="Hosts & properties" subtitle="Where the volume, the problems and the cost are" />
      {error && <ErrorState error={error} />}
      {loading && !data ? (
        <LoadingGrid cards={2} />
      ) : data ? (
        <>
          <Section title="Hosts" description="Sorted by call volume. Cost covers metered usage plus telephony minutes.">
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
