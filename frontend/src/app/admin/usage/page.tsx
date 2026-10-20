"use client";

import Link from "next/link";
import { AlertTriangle, IndianRupee, Phone, Receipt } from "lucide-react";
import { StatCard } from "@/components/stat-card";
import { useAdminQuery } from "@/components/admin/admin-context";
import { BarList, ErrorState, Histogram, LoadingGrid, PageHeader, Section, fmtMoney, fmtNum, humanize } from "@/components/admin/admin-ui";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { adminApi } from "@/lib/admin-api";

const UNIT_LABELS: Record<string, string> = {
  prompt_tokens: "input tokens",
  completion_tokens: "output tokens",
  audio_seconds: "audio seconds",
  characters: "characters",
  minutes: "minutes",
  messages: "messages",
  emails: "emails",
  requests: "requests",
  records: "records",
  scrapes: "scrapes",
};

export default function AdminUsagePage() {
  const { data, loading, error } = useAdminQuery(adminApi.usage);
  const unpriced = data?.by_account.filter((a) => a.unpriced_units.length > 0) ?? [];

  return (
    <>
      <PageHeader title="Usage & cost" subtitle="Metered usage of every paid service, priced with your Settings" />
      {error && <ErrorState error={error} />}
      <div className="grid gap-3 sm:grid-cols-3">
        <StatCard icon={IndianRupee} label="Total spend" value={fmtMoney(data?.totals.cost_inr)} loading={loading} />
        <StatCard icon={Phone} iconColorVar="--status-live" label="Answered phone calls" value={fmtNum(data?.totals.answered_phone_calls)} loading={loading} />
        <StatCard icon={Receipt} label="Cost per answered call" value={fmtMoney(data?.totals.cost_per_answered_call_inr)} loading={loading} />
      </div>
      {loading && !data ? (
        <LoadingGrid cards={2} />
      ) : data ? (
        <>
          {unpriced.length > 0 && (
            <div className="flex items-start gap-2 rounded-lg bg-[var(--status-pending-bg)] px-3 py-2 text-sm">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-[var(--status-pending)]" />
              <span>
                No unit price set for {unpriced.map((a) => `${a.label} (${a.unpriced_units.join(", ")})`).join("; ")} — usage is counted but
                costed at ₹0. Set prices on the{" "}
                <Link href="/admin/balances" className="underline underline-offset-2">
                  Balances
                </Link>{" "}
                page.
              </span>
            </div>
          )}
          <div className="grid gap-5 lg:grid-cols-2">
            <Section title="Spend by service" description={`In INR · USD converted at ₹${data.usd_inr}`}>
              <BarList
                colorVar="--accent-warm"
                items={data.by_account.map((a) => ({
                  label: a.label,
                  value: a.cost_inr,
                  hint: a.currency === "USD" ? `($${a.cost.toFixed(2)})` : undefined,
                }))}
                format={(v) => fmtMoney(v)}
                emptyLabel="No metered usage in this range"
              />
            </Section>
            <Section title="Daily spend" description="INR per day">
              <Histogram
                buckets={data.daily.map((d, i) => ({ from: i, to: i + 1, count: Math.round(d.cost_inr) }))}
                label={(b) => {
                  const d = data.daily[b.from];
                  return data.daily.length <= 14 || b.from % Math.ceil(data.daily.length / 10) === 0 ? d.date.slice(5) : "";
                }}
                colorVar="--accent-warm"
              />
            </Section>
          </div>
          <Section title="Line items" description="Every metered unit, its price and cost. Telephony minutes are derived from call durations.">
            {data.rows.length === 0 ? (
              <p className="py-4 text-center text-sm text-muted-foreground">No metered usage in this range</p>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Service</TableHead>
                      <TableHead>Model</TableHead>
                      <TableHead className="text-right">Quantity</TableHead>
                      <TableHead className="text-right">Unit price</TableHead>
                      <TableHead className="text-right">Cost</TableHead>
                      <TableHead className="text-right">Cost (₹)</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.rows.map((r) => (
                      <TableRow key={`${r.service}-${r.unit}-${r.model}`}>
                        <TableCell>
                          <p className="font-medium">{humanize(r.service)}</p>
                          <p className="text-xs text-muted-foreground">{UNIT_LABELS[r.unit] ?? r.unit}</p>
                        </TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">{r.model ?? "—"}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmtNum(r.quantity, 1)}</TableCell>
                        <TableCell className="text-right tabular-nums text-muted-foreground">
                          {r.priced ? `${r.currency === "USD" ? "$" : "₹"}${r.unit_price.toPrecision(3)}` : "not set"}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{fmtMoney(r.cost, r.currency)}</TableCell>
                        <TableCell className="text-right tabular-nums font-medium">{fmtMoney(r.cost_inr)}</TableCell>
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
